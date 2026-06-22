import frappe
from frappe import _
from frappe.utils import cint


# ---------------------------------------------------------------------------
# Item validation
# ---------------------------------------------------------------------------

def validate_item_type(doc, _method=None):
    if not doc.is_stock_item and not doc.is_fixed_asset and not doc.get("custom_is_service_item"):
        frappe.throw(
            "Please define the item type. Check one of:"
            "<br><br>&bull;&nbsp;<b>Maintain Stock</b>"
            "<br>&bull;&nbsp;<b>Is Fixed Asset</b>"
            "<br>&bull;&nbsp;<b>Is Service Item</b>",
            title="Item Type Required"
        )


# ---------------------------------------------------------------------------
# Withholding helpers
# ---------------------------------------------------------------------------

def _is_service_only_category(category_name):
    """Return True if the Tax Withholding Category is marked Applicable For Services."""
    if not category_name:
        return False
    return cint(frappe.db.get_value(
        "Tax Withholding Category",
        category_name,
        "custom_applicable_for_services",
    ))


def _get_active_withholding_categories(doc):
    """
    Return all withholding categories active on the document.

    Covers:
      - doc.tax_withholding_category  (standard ERPNext field)
      - doc.custom_withholding_1/2/3  (custom multiple-withholding fields)
    """
    seen = set()
    cats = []
    for field in ("tax_withholding_category", "custom_withholding_1", "custom_withholding_2", "custom_withholding_3"):
        val = doc.get(field)
        if val and val not in seen:
            seen.add(val)
            cats.append(val)
    return cats


def _build_account_category_map(categories, company):
    """Return {account_head: category_name} for all active withholding categories."""
    account_map = {}
    for cat in categories:
        rows = frappe.get_all(
            "Tax Withholding Account",
            filters={"parent": cat, "company": company},
            fields=["account"],
        )
        for r in rows:
            if r.account:
                account_map[r.account] = cat
    return account_map


def _get_all_withholding_accounts(company):
    """
    Return all account heads that appear in any Tax Withholding Category for the company.
    Used to detect withholding rows in the taxes table without relying on any flag on
    the row itself (which is absent for rows added via custom multiple-withholding fields).
    """
    rows = frappe.get_all(
        "Tax Withholding Account",
        filters={"company": company},
        fields=["account"],
    )
    return {r.account for r in rows if r.account}


def _get_withholding_rate_for_date(category_name, posting_date):
    """Return the applicable withholding rate for a category on the given posting date."""
    try:
        category = frappe.get_cached_doc("Tax Withholding Category", category_name)
    except Exception:
        return None
    if not category.rates:
        return None
    current_date = frappe.utils.getdate(posting_date or frappe.utils.nowdate())
    for rate_row in category.rates:
        from_date = frappe.utils.getdate(rate_row.from_date) if rate_row.from_date else None
        to_date = frappe.utils.getdate(rate_row.to_date) if rate_row.to_date else None
        if from_date and current_date < from_date:
            continue
        if to_date and current_date > to_date:
            continue
        return rate_row.tax_withholding_rate
    return category.rates[-1].tax_withholding_rate


# ---------------------------------------------------------------------------
# before_validate hooks — run BEFORE ERPNext's calculate_taxes_and_totals()
# ---------------------------------------------------------------------------

def apply_additional_withholding_rows(doc, _method=None):
    """
    Add a tax row for each custom_withholding_1/2/3 category that does not yet
    have a corresponding row in the taxes table.

    Must run in before_validate so the rows are present when ERPNext's
    calculate_taxes_and_totals() fires, ensuring they are included in the
    grand total on the very first save.
    """
    if not doc.get("apply_tds") or not doc.get("apply_multiple_withholding"):
        return

    custom_cats = [
        doc.get(f)
        for f in ("custom_withholding_1", "custom_withholding_2", "custom_withholding_3")
        if doc.get(f)
    ]
    if not custom_cats:
        return

    existing_accounts = {t.account_head for t in (doc.taxes or [])}

    all_tds_net = sum(
        (row.net_amount or 0) for row in (doc.items or []) if cint(row.get("apply_tds"))
    )
    service_tds_net = sum(
        (row.net_amount or 0)
        for row in (doc.items or [])
        if cint(row.get("apply_tds")) and cint(row.get("custom_is_service_item"))
    )

    for cat_name in custom_cats:
        wh_accounts = frappe.get_all(
            "Tax Withholding Account",
            filters={"parent": cat_name, "company": doc.company},
            fields=["account"],
            limit=1,
        )
        if not wh_accounts or not wh_accounts[0].account:
            frappe.throw(
                _("No withholding account configured for <b>{0}</b> for company <b>{1}</b>. "
                  "Please set it up in the Tax Withholding Category.").format(cat_name, doc.company),
                title=_("Withholding Account Missing"),
            )

        account = wh_accounts[0].account
        if account in existing_accounts:
            continue

        rate = _get_withholding_rate_for_date(cat_name, doc.posting_date) or 0
        is_service_only = _is_service_only_category(cat_name)
        base = service_tds_net if is_service_only else all_tds_net

        if is_service_only and base == 0:
            # No qualifying service items — skip adding the row; the validate hook
            # will throw an informative error so the user can act on it.
            continue

        tax_amount = round(base * rate / 100, 2)
        base_tax_amount = round(tax_amount * (doc.conversion_rate or 1), 2)

        row = {
            "charge_type": "Actual",
            "add_deduct_tax": "Deduct",
            "category": "Total",
            "account_head": account,
            "description": "Withholding Tax - {}".format(cat_name),
            "rate": rate,
            "tax_amount": tax_amount,
            "tax_amount_after_discount_amount": tax_amount,
            "base_tax_amount": base_tax_amount,
            "base_tax_amount_after_discount_amount": base_tax_amount,
        }

        # Copy accounting dimensions from the invoice header
        for dim in frappe.get_all("Accounting Dimension", fields=["fieldname"]):
            if doc.get(dim.fieldname):
                row[dim.fieldname] = doc.get(dim.fieldname)

        doc.append("taxes", row)
        existing_accounts.add(account)


def remove_orphaned_withholding_tax_rows(doc, _method=None):
    """
    Remove withholding tax rows whose account no longer belongs to any active
    withholding category on the document.

    Uses the Account master (is_tax_withholding_account flag) to detect withholding
    rows reliably — does NOT depend on the is_tax_withholding_account flag on the
    tax row itself, which is absent for rows added via the custom multiple-withholding
    fields.
    """
    taxes = doc.get("taxes") or []
    if not taxes:
        return

    all_wh_accounts = _get_all_withholding_accounts(doc.company)
    if not all_wh_accounts:
        return

    categories = _get_active_withholding_categories(doc)
    active_accounts = set()
    if categories:
        active_accounts = set(_build_account_category_map(categories, doc.company).keys())

    new_taxes = [
        t for t in taxes
        if t.account_head not in all_wh_accounts or t.account_head in active_accounts
    ]

    if len(new_taxes) != len(taxes):
        doc.taxes = new_taxes
        for i, t in enumerate(doc.taxes):
            t.idx = i + 1


def sync_is_service_item_on_pi(doc, _method=None):
    """Sync custom_is_service_item on PI/PO item rows from the Item master."""
    items = getattr(doc, "items", [])
    if not items:
        return

    item_codes = {row.item_code for row in items if row.get("item_code")}
    if not item_codes:
        return

    results = frappe.get_all(
        "Item",
        filters={"name": ["in", list(item_codes)]},
        fields=["name", "custom_is_service_item"],
    )
    service_map = {r["name"]: cint(r["custom_is_service_item"]) for r in results}

    for row in items:
        if row.get("item_code"):
            row.custom_is_service_item = service_map.get(row.item_code, 0)


def sync_tds_from_item_tax_template(doc, method=None):
    """
    Set apply_tds on each item row directly from its Item Tax Template.

    apply_tds is controlled solely by the Item Tax Template's apply_tds field.
    The Tax Withholding Category selected on the invoice (including whether it is
    service-only) does not change which items have apply_tds checked.
    """
    items = getattr(doc, "items", [])
    if not items:
        return

    templates = {row.item_tax_template for row in items if row.get("item_tax_template")}
    template_tds_map = {}
    if templates:
        results = frappe.get_all(
            "Item Tax Template",
            filters={"name": ["in", list(templates)]},
            fields=["name", "apply_tds"],
        )
        template_tds_map = {r["name"]: cint(r["apply_tds"]) for r in results}

    for row in items:
        template = row.get("item_tax_template")
        row.apply_tds = template_tds_map.get(template, 0) if template else 0


def recalculate_withholding_tax_amounts(doc, _method=None):
    """
    Recalculate tax_amount on every withholding row from the current item totals.

    Service-only categories (custom_applicable_for_services = 1):
        base = net_amount sum for items where apply_tds=1 AND custom_is_service_item=1
        If base = 0 (no qualifying service items), the row is REMOVED.

    All-item categories:
        base = net_amount sum for items where apply_tds=1

    Detects withholding rows via account_category_map (does not depend on the
    is_tax_withholding_account flag on the row, which is absent for rows added via
    the custom multiple-withholding fields).
    """
    taxes = doc.get("taxes") or []
    categories = _get_active_withholding_categories(doc)
    if not categories:
        return

    account_category_map = _build_account_category_map(categories, doc.company)
    if not account_category_map:
        return

    all_tds_net = sum(
        (row.net_amount or 0) for row in (doc.items or []) if cint(row.get("apply_tds"))
    )
    service_tds_net = sum(
        (row.net_amount or 0)
        for row in (doc.items or [])
        if cint(row.get("apply_tds")) and cint(row.get("custom_is_service_item"))
    )

    new_taxes = []
    modified = False

    for tax in taxes:
        cat_name = account_category_map.get(tax.account_head)
        if not cat_name:
            new_taxes.append(tax)
            continue

        rate = _get_withholding_rate_for_date(cat_name, doc.posting_date)
        if not rate:
            new_taxes.append(tax)
            continue

        tax.rate = rate
        is_service_only = _is_service_only_category(cat_name)
        base = service_tds_net if is_service_only else all_tds_net

        if is_service_only and base == 0:
            modified = True  # drop this row — no qualifying service items
        else:
            new_amount = round(base * rate / 100, 2)
            base_amount = round(new_amount * (doc.conversion_rate or 1), 2)
            if tax.tax_amount != new_amount:
                tax.tax_amount = new_amount
                tax.tax_amount_after_discount_amount = new_amount
                tax.base_tax_amount = base_amount
                tax.base_tax_amount_after_discount_amount = base_amount
                modified = True
            new_taxes.append(tax)

    if modified:
        doc.taxes = new_taxes
        for i, t in enumerate(doc.taxes):
            t.idx = i + 1


# ---------------------------------------------------------------------------
# before_save hooks
# ---------------------------------------------------------------------------

def set_gross_amount(doc, _method=None):
    net_total = doc.net_total or 0
    added = doc.taxes_and_charges_added or 0
    doc.gross_amount = net_total + added


# ---------------------------------------------------------------------------
# validate hooks — run AFTER ERPNext's calculate_taxes_and_totals()
# ---------------------------------------------------------------------------

def validate_service_withholding_category(doc, _method=None):
    """
    Inform (non-blocking) if a service-only withholding category is active
    but no item has both apply_tds = 1 and custom_is_service_item = 1.

    Service-only categories base withholding on service items with apply_tds = 1.
    Categories without the service-only flag base withholding on all apply_tds items.
    """
    if not doc.get("apply_tds"):
        return

    categories = _get_active_withholding_categories(doc)
    service_only_cats = [c for c in categories if _is_service_only_category(c)]
    if not service_only_cats:
        return

    has_qualifying_item = any(
        cint(row.get("apply_tds")) and cint(row.get("custom_is_service_item"))
        for row in (doc.items or [])
    )

    if not has_qualifying_item:
        cats_html = ", ".join("<b>{}</b>".format(c) for c in service_only_cats)
        frappe.msgprint(
            _(
                "No service item in this invoice is applicable to withholding. "
                "The following withholding categories apply to service items only: {0}. "
                "Withholding will not be calculated for these categories."
            ).format(cats_html),
            title=_("No Applicable Service Items for Withholding"),
            indicator="orange",
        )


def set_withholding_tax_rates(doc, _method=None):
    """
    Set the rate field on withholding tax rows where rate is 0.

    Looks up the applicable rate from the Tax Withholding Category via account_head
    rather than parsing the description string. Runs in validate so the rate is
    persisted with the document on every save.
    """
    taxes = doc.get("taxes") or []
    categories = _get_active_withholding_categories(doc)
    if not categories:
        return

    account_category_map = _build_account_category_map(categories, doc.company)

    for tax in taxes:
        if tax.rate and tax.rate > 0:
            continue
        cat_name = account_category_map.get(tax.account_head)
        if not cat_name:
            continue
        rate = _get_withholding_rate_for_date(cat_name, doc.posting_date)
        if rate:
            tax.rate = rate


def validate_withholding_in_taxes_table(doc, _method=None):
    """
    For every withholding-account tax row in the taxes table:
    - Charge type must be 'Actual'.
    - Add/Deduct must be 'Deduct'.

    Negative amounts are not corrected — debit notes and returns legitimately
    carry negative withholding amounts to reverse the original deduction.
    """
    all_wh_accounts = _get_all_withholding_accounts(doc.company)

    for tax in doc.get("taxes") or []:
        if tax.account_head not in all_wh_accounts:
            continue

        if tax.charge_type != "Actual":
            frappe.throw(
                _(
                    "Withholding Tax row <b>{0}</b> must use charge type <b>Actual</b>. "
                    "Current charge type is <b>{1}</b>. Please update the taxes row."
                ).format(tax.account_head, tax.charge_type),
                title=_("Invalid Withholding Tax Setup"),
            )

        if tax.add_deduct_tax != "Deduct":
            frappe.throw(
                _(
                    "Withholding Tax row <b>{0}</b> must be set to <b>Deduct</b>. "
                    "Current setting is <b>{1}</b>. Please update the taxes row."
                ).format(tax.account_head, tax.add_deduct_tax),
                title=_("Invalid Withholding Tax Setup"),
            )

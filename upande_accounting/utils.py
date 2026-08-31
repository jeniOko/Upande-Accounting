import frappe
from frappe import _
from frappe.utils import cint, flt


# ---------------------------------------------------------------------------
# Withholding category normalization — must run FIRST in before_validate
# ---------------------------------------------------------------------------

def normalize_withholding_categories(doc, _method=None):
    """
    ERPNext's set_tax_withholding() bases its calculation on items with apply_tds=1.
    A service-only category in tax_withholding_category would therefore produce 0 or be
    dropped, causing a debit/credit mismatch at submit time.

    Guarantee tax_withholding_category never holds a service-only category:
    - If a non-service-only category exists in custom_withholding_1/2/3, swap — the
      non-service one moves to tax_withholding_category, the service-only one takes
      its place in the custom field.
    - If no swap candidate exists, move the service-only category to the first empty
      custom field and clear tax_withholding_category.
    """
    std_cat = doc.get("tax_withholding_category")
    if not std_cat or not _is_service_only_category(std_cat):
        return

    custom_fields = ["custom_withholding_1", "custom_withholding_2", "custom_withholding_3"]

    # Prefer swapping with a non-service-only custom category
    for f in custom_fields:
        val = doc.get(f)
        if val and not _is_service_only_category(val):
            doc.tax_withholding_category = val
            doc.set(f, std_cat)
            return

    # No swap candidate — move to first empty custom field and uncheck apply_tds so
    # ERPNext's set_tax_withholding() is fully bypassed (empty standard category + unchecked).
    for f in custom_fields:
        if not doc.get(f):
            doc.set(f, std_cat)
            doc.tax_withholding_category = ""
            doc.apply_tds = 0
            doc.apply_multiple_withholding = 1
            slot = custom_fields.index(f) + 1
            if int(doc.get("custom_withholding_count") or 0) < slot:
                doc.custom_withholding_count = str(slot)
            return

    frappe.throw(
        _(
            "Withholding category <b>{0}</b> is applicable for services only and cannot "
            "remain in the standard field — all custom withholding fields are occupied. "
            "Please free up one custom field to make room."
        ).format(std_cat),
        title=_("Withholding Category Conflict"),
    )


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


# Fields any caller of category_base_from_item_rows needs from each Purchase Invoice
# Item row (in addition to the amount field itself). Exposed so report queries fetch
# exactly what the shared calculation reads — see the withholding KRA/VAT/register reports.
WITHHOLDING_OVERRIDE_FIELDS = (
    "apply_tds",
    "custom_is_service_item",
    "custom_override_withholding",
    "custom_withholding_action",
    "custom_withholding_override_category",
)


def category_base_from_item_rows(item_rows, cat_name, is_service_only, amount_field="net_amount"):
    """
    Sum `amount_field` over item_rows for a single withholding category, honoring
    per-item withholding overrides (custom_override_withholding /
    custom_withholding_action / custom_withholding_override_category).

    Default inclusion rule (unchanged from before item-level overrides existed):
      - service-only category (custom_applicable_for_services=1): items with
        custom_is_service_item=1
      - all other categories: items with apply_tds=1

    An item row with custom_override_withholding=1 whose
    custom_withholding_override_category matches cat_name replaces the default rule for
    that item only (does not affect any other category active on the same document):
      - custom_withholding_action="Apply"  -> item is always included in this category's
        base, regardless of apply_tds / custom_is_service_item.
      - custom_withholding_action="Ignore" -> item is always excluded from this category's
        base, regardless of apply_tds / custom_is_service_item.

    item_rows may be live Document child rows (Purchase Invoice.items) or plain dicts
    fetched via frappe.get_all — both support .get(). This is the single source of truth
    for the base calculation, shared by the live document hooks (_get_category_base_net)
    and the withholding KRA/VAT/register reports, so a report can never disagree with
    what was actually withheld on the invoice.
    """
    total = 0.0

    for row in item_rows:
        amount = flt(row.get(amount_field))

        if (
            cint(row.get("custom_override_withholding"))
            and row.get("custom_withholding_override_category") == cat_name
        ):
            if row.get("custom_withholding_action") == "Apply":
                total += amount
            continue

        if is_service_only:
            if cint(row.get("custom_is_service_item")):
                total += amount
        else:
            if cint(row.get("apply_tds")):
                total += amount

    return total


def _get_category_base_net(doc, cat_name):
    """Net amount (document currency) base for cat_name on a live Purchase Invoice doc."""
    is_service_only = _is_service_only_category(cat_name)
    return category_base_from_item_rows(doc.items or [], cat_name, is_service_only, amount_field="net_amount")


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


def validate_item_withholding_overrides(doc, _method=None):
    """
    Validate per-item withholding overrides (custom_override_withholding).

    - When the override is checked on a row, both the action (Apply/Ignore) and the
      target Tax Withholding Category are required — a half-set override is ambiguous
      and would silently be ignored by _get_category_base_net.
    - The target category must be one of the categories currently active on the document
      (tax_withholding_category / custom_withholding_1/2/3). An override referencing a
      category that isn't active would otherwise silently do nothing, masking the
      user's intent to include/exclude that item.

    Must run after normalize_withholding_categories (so tax_withholding_category has
    already been finalized) and before the base-amount calculations that consume the
    overrides (apply_additional_withholding_rows / recalculate_withholding_tax_amounts).
    """
    items = doc.get("items") or []
    if not any(cint(row.get("custom_override_withholding")) for row in items):
        return

    active_categories = set(_get_active_withholding_categories(doc))

    for row in items:
        if not cint(row.get("custom_override_withholding")):
            continue

        action = row.get("custom_withholding_action")
        category = row.get("custom_withholding_override_category")

        if not action or not category:
            frappe.throw(
                _(
                    "Row #{0}: When <b>Override Withholding Treatment</b> is checked, both "
                    "the withholding action (Apply/Ignore) and the Withholding Category "
                    "must be set."
                ).format(row.idx),
                title=_("Incomplete Withholding Override"),
            )

        if category not in active_categories:
            frappe.throw(
                _(
                    "Row #{0}: Withholding override references category <b>{1}</b>, which is "
                    "not active on this invoice. Select an active withholding category "
                    "(Tax Withholding Category or one of the Custom Withholding fields), or "
                    "uncheck the override for this item."
                ).format(row.idx, category),
                title=_("Invalid Withholding Override"),
            )


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


def apply_additional_withholding_rows(doc, _method=None):
    """
    Ensure tax rows exist for withholding categories that our custom logic owns:

    - custom_withholding_1/2/3 (when apply_multiple_withholding=1): always.
    - tax_withholding_category when it is service-only: ERPNext bases its
      calculation on apply_tds items and may produce 0 or skip the row entirely
      if no items have apply_tds=1; we guarantee the row exists so that
      recalculate_withholding_tax_amounts can set the correct service-based amount.

    Per-category base amounts (and therefore whether a service-only row is skipped)
    come from _get_category_base_net, which honors item-level withholding overrides —
    see validate_item_withholding_overrides / _get_category_base_net.

    Must run before recalculate_withholding_tax_amounts.
    """
    if not doc.get("apply_tds") and not doc.get("apply_multiple_withholding"):
        return

    cats_to_ensure = []

    # Standard category — only when service-only (ERPNext handles the normal case)
    std_cat = doc.get("tax_withholding_category")
    if std_cat and _is_service_only_category(std_cat):
        cats_to_ensure.append(std_cat)

    # Custom multiple-withholding categories
    if doc.get("apply_multiple_withholding"):
        seen = {c for c in cats_to_ensure}
        for f in ("custom_withholding_1", "custom_withholding_2", "custom_withholding_3"):
            val = doc.get(f)
            if val and val not in seen:
                cats_to_ensure.append(val)
                seen.add(val)

    if not cats_to_ensure:
        return

    existing_accounts = {t.account_head for t in (doc.taxes or [])}

    for cat_name in cats_to_ensure:
        wh_accounts = frappe.get_all(
            "Tax Withholding Account",
            filters={"parent": cat_name, "company": doc.company},
            fields=["account"],
            limit=1,
        )
        if not wh_accounts or not wh_accounts[0].account:
            frappe.throw(
                _(
                    "No withholding account configured for <b>{0}</b> for company <b>{1}</b>. "
                    "Please set it up in the Tax Withholding Category."
                ).format(cat_name, doc.company),
                title=_("Withholding Account Missing"),
            )

        account = wh_accounts[0].account
        if account in existing_accounts:
            continue

        rate = _get_withholding_rate_for_date(cat_name, doc.posting_date) or 0
        is_service_only = _is_service_only_category(cat_name)
        base = _get_category_base_net(doc, cat_name)

        if is_service_only and base == 0:
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

        for dim in frappe.get_all("Accounting Dimension", fields=["fieldname"]):
            if doc.get(dim.fieldname):
                row[dim.fieldname] = doc.get(dim.fieldname)

        doc.append("taxes", row)
        existing_accounts.add(account)


def recalculate_withholding_tax_amounts(doc, _method=None):
    """
    Recalculate tax_amount on every withholding row from the current item totals.

    Service-only categories (custom_applicable_for_services = 1):
        base = net_amount sum for items where custom_is_service_item=1 (apply_tds not required)
        If base = 0 (no qualifying service items), the row is REMOVED.

    All-item categories:
        base = net_amount sum for items where apply_tds=1

    In both cases, an item's custom_override_withholding (Apply/Ignore, targeting this
    exact category) overrides the default inclusion rule for that item — see
    _get_category_base_net, which computes the base per category.

    Detects withholding rows via account_category_map (does not depend on the
    is_tax_withholding_account flag on the row, which is absent for rows added via
    the custom multiple-withholding fields).

    Runs twice in the doctype lifecycle (see hooks.py):
      - before_validate: corrects the service-only / custom_withholding_1-3 rows this
        app itself appends, before ERPNext's calculate_taxes_and_totals() sums the doc.
      - validate (after ERPNext's own PurchaseInvoice.set_tax_withholding()): the plain
        single-category case (tax_withholding_category, non-service) is entirely computed
        by ERPNext's native logic, which knows nothing about item-level overrides. Re-running
        here corrects that row too, so an override on a plain non-service category isn't a
        silent no-op. Because this second pass runs after ERPNext already summed totals from
        the uncorrected amount, doc.calculate_taxes_and_totals() is invoked again whenever a
        row actually changes — the same pattern ERPNext's own set_tax_withholding() uses
        after it mutates the taxes table.
    """
    taxes = doc.get("taxes") or []
    categories = _get_active_withholding_categories(doc)
    if not categories:
        return

    account_category_map = _build_account_category_map(categories, doc.company)
    if not account_category_map:
        return

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
        base = _get_category_base_net(doc, cat_name)

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
        doc.calculate_taxes_and_totals()


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
    Inform (non-blocking) if a service-only withholding category ends up with a zero
    base — either because no item is marked as a service item, or because item-level
    withholding overrides (custom_override_withholding) ignore every otherwise-qualifying
    item for that category.

    Service-only categories base withholding on all service items (custom_is_service_item=1),
    subject to per-item overrides — see _get_category_base_net. The item's apply_tds flag is
    not required — the category's service-only flag is sufficient. Categories without the
    service-only flag base withholding on all apply_tds items (also subject to overrides).
    """
    if not doc.get("apply_tds") and not doc.get("apply_multiple_withholding"):
        return

    categories = _get_active_withholding_categories(doc)
    service_only_cats = [c for c in categories if _is_service_only_category(c)]
    if not service_only_cats:
        return

    non_qualifying_cats = [c for c in service_only_cats if _get_category_base_net(doc, c) == 0]

    if non_qualifying_cats:
        cats_html = ", ".join("<b>{}</b>".format(c) for c in non_qualifying_cats)
        frappe.msgprint(
            _(
                "No item in this invoice is applicable to withholding (after item-level "
                "overrides, if any). The following withholding categories apply to service "
                "items only: {0}. Withholding will not be calculated for these categories."
            ).format(cats_html),
            title=_("No Applicable Items for Withholding"),
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

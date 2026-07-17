import frappe
from frappe import _
from frappe.utils import cint


# ---------------------------------------------------------------------------
# Withholding category normalization — must run FIRST in before_validate,
# before ERPNext's own PurchaseTaxWithholding engine runs inside validate()
# ---------------------------------------------------------------------------

def normalize_withholding_categories_on_items(doc, _method=None):
    """
    ERPNext v16's tax withholding engine (PurchaseTaxWithholding) bases its
    calculation per item strictly on rows where apply_tds=1 — it has no concept
    of a "service-only" category that should apply regardless of apply_tds.
    A service-only category sitting in an item's native tax_withholding_category
    slot would therefore silently exclude any service item with apply_tds unchecked.

    Only rows where apply_tds is unchecked are at risk — if apply_tds=1, core
    already includes the item in its category's base regardless of the
    service-only flag (core has no notion of that flag either way), so no
    swap is needed there.

    Guarantee tax_withholding_category never holds a service-only category on
    a row where it would actually be dropped, per item:
    - If a non-service-only category exists in that row's custom_withholding_2/3,
      swap — the non-service one moves to tax_withholding_category, the service-only
      one takes its place in the additional slot.
    - If no swap candidate exists, move the service-only category to the first empty
      additional slot on that row and clear tax_withholding_category.
    """
    additional_fields = ["custom_withholding_2", "custom_withholding_3"]

    for item in doc.get("items") or []:
        std_cat = item.get("tax_withholding_category")
        if not std_cat or not _is_service_only_category(std_cat):
            continue
        if item.get("apply_tds"):
            continue

        swapped = False
        for f in additional_fields:
            val = item.get(f)
            if val and not _is_service_only_category(val):
                item.tax_withholding_category = val
                item.set(f, std_cat)
                swapped = True
                break
        if swapped:
            continue

        for f in additional_fields:
            if not item.get(f):
                item.set(f, std_cat)
                item.tax_withholding_category = ""
                swapped = True
                break

        if not swapped:
            frappe.throw(
                _(
                    "Row #{0}: Withholding category <b>{1}</b> is applicable for services only "
                    "and cannot remain in the item's standard Tax Withholding Category field — "
                    "both additional withholding slots on this row are occupied. Please free up "
                    "one to make room."
                ).format(item.idx, std_cat),
                title=_("Withholding Category Conflict"),
            )


def sync_header_apply_tds_from_items(doc, _method=None):
    """
    ERPNext's own PurchaseTaxWithholding engine gates its entire native-category
    computation on the header-level apply_tds checkbox — separate from each
    item's own apply_tds flag, which only decides whether that specific item is
    included once the header switch is already on. Core only auto-checks the
    header box from Supplier.tax_withholding_category/tax_withholding_group
    (PurchaseInvoice.set_missing_values()); when a category is instead assigned
    directly on items — our normal workflow — nothing else turns it on, so
    core's engine silently never runs.

    Turn it on whenever any item ends up with a native tax_withholding_category
    and apply_tds checked, after normalize_withholding_categories_on_items has
    settled which items still carry one. Never turned back off here — an
    explicit uncheck by the user is left alone if no item currently qualifies.
    """
    if doc.get("apply_tds"):
        return
    for item in doc.get("items") or []:
        if item.get("apply_tds") and item.get("tax_withholding_category"):
            doc.apply_tds = 1
            return


def sync_additional_withholding_categories_to_items(doc, _method=None):
    """
    Cascade the invoice-level 'additional withholding' category defaults
    (custom_withholding_2/3) down onto every item row that qualifies for them,
    and clear them from rows that don't. The header fields are the single
    source of truth for which additional categories apply to this invoice —
    per-item qualification is re-derived on every save so stale values never
    survive, mirroring sync_tds_from_item_tax_template's pattern.

    Qualification rule per category:
      - service-only category (custom_applicable_for_services=1): the row must
        have custom_is_service_item=1.
      - any other category: the row must have apply_tds=1.

    Must run after normalize_withholding_categories_on_items (so the native
    slot is already settled) and before apply_additional_withholding_rows.
    """
    items = doc.get("items") or []
    if not items:
        return

    slots = ("custom_withholding_2", "custom_withholding_3")
    count = cint(doc.get("custom_withholding_count") or 0) if doc.get("apply_multiple_withholding") else 0

    header_defaults = {}
    if count >= 1:
        header_defaults["custom_withholding_2"] = doc.get("custom_withholding_2") or ""
    if count >= 2:
        header_defaults["custom_withholding_3"] = doc.get("custom_withholding_3") or ""

    for item in items:
        for slot in slots:
            default_cat = header_defaults.get(slot, "")
            if not default_cat or default_cat == item.get("tax_withholding_category"):
                item.set(slot, "")
                continue

            qualifies = (
                cint(item.get("custom_is_service_item"))
                if _is_service_only_category(default_cat)
                else cint(item.get("apply_tds"))
            )
            item.set(slot, default_cat if qualifies else "")


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


def _get_additional_categories_from_items(doc):
    """
    Return the set of 'additional' withholding categories currently carried by
    any item's custom_withholding_2/3 slot. These are the categories our own
    logic (apply_additional_withholding_rows / recalculate_withholding_tax_amounts)
    is responsible for — layered on top of each item's native tax_withholding_category,
    which ERPNext's own PurchaseTaxWithholding engine owns entirely.
    """
    categories = set()
    for item in doc.get("items") or []:
        for slot in ("custom_withholding_2", "custom_withholding_3"):
            val = item.get(slot)
            if val:
                categories.add(val)
    return categories


def _get_native_categories_from_items(doc):
    """Return the set of categories currently used as any item's native tax_withholding_category."""
    return {
        item.get("tax_withholding_category")
        for item in (doc.get("items") or [])
        if item.get("tax_withholding_category")
    }


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
# validate hooks (amount-dependent) — registered to run AFTER ERPNext's own
# PurchaseInvoice.validate() completes (doc_events "validate" hooks always run
# after the doctype's own validate() method in the same call), so item.net_amount
# and ERPNext's own native-category tax_withholding_entries/taxes rows already
# exist by the time these run.
# ---------------------------------------------------------------------------

def remove_orphaned_withholding_tax_rows(doc, _method=None):
    """
    Remove 'additional' withholding tax rows (the ones apply_additional_withholding_rows
    creates) whose category is no longer carried by any item's custom_withholding_2/3
    slot.

    Never touches an account that's currently used as any item's native
    tax_withholding_category — those rows belong entirely to ERPNext's own
    PurchaseTaxWithholding engine, which has already added/updated/removed them
    itself inside PurchaseInvoice.validate(), before this validate-stage hook runs.
    """
    taxes = doc.get("taxes") or []
    if not taxes:
        return

    all_wh_accounts = _get_all_withholding_accounts(doc.company)
    if not all_wh_accounts:
        return

    native_categories = _get_native_categories_from_items(doc)
    native_accounts = (
        set(_build_account_category_map(native_categories, doc.company).keys())
        if native_categories else set()
    )

    additional_categories = _get_additional_categories_from_items(doc)
    active_additional_accounts = (
        set(_build_account_category_map(additional_categories, doc.company).keys())
        if additional_categories else set()
    )

    manageable_accounts = all_wh_accounts - native_accounts

    new_taxes = [
        t for t in taxes
        if t.account_head not in manageable_accounts or t.account_head in active_additional_accounts
    ]

    if len(new_taxes) != len(taxes):
        doc.taxes = new_taxes
        for i, t in enumerate(doc.taxes):
            t.idx = i + 1


# ---------------------------------------------------------------------------
# back to before_validate hooks — these two are plain master-data syncs (Item /
# Item Tax Template lookups), no dependency on net_amount, so they stay early.
# ---------------------------------------------------------------------------

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


def apply_additional_withholding_rows(doc, _method=None):
    """
    Ensure a tax row exists for every 'additional' withholding category present
    on any item's custom_withholding_2/3 slot — the categories layered on top of
    an item's own native tax_withholding_category. ERPNext's core tax withholding
    engine has no concept of a second category per item, so we materialize these
    rows ourselves.

    Sets is_tax_withholding_account=1 so make_gl_entries_for_tax_withholding()
    books GL for these rows exactly like ERPNext's own native-category rows.
    Safe from ERPNext's own update_tax_rows(): that method only touches accounts
    belonging to categories used as an item's *native* tax_withholding_category,
    so as long as an additional category's account differs from any native
    category's account on this invoice (the normal setup — each category has
    its own dedicated account), our rows are left alone.

    Reads item.net_amount, so must run in the "validate" doc_event stage (after
    ERPNext's own PurchaseInvoice.validate() has run calculate_taxes_and_totals()
    at least once) — before_validate is too early, net_amount is still 0 there.
    Must run before recalculate_withholding_tax_amounts, and finalize_additional_withholding_totals
    must run after both, to refresh totals from the rows they add/change.
    """
    items = doc.get("items") or []
    if not items:
        return

    cats_to_ensure = _get_additional_categories_from_items(doc)
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

        base = sum(
            (item.net_amount or 0)
            for item in items
            if item.get("custom_withholding_2") == cat_name or item.get("custom_withholding_3") == cat_name
        )
        if base == 0:
            continue

        rate = _get_withholding_rate_for_date(cat_name, doc.posting_date) or 0
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
            "is_tax_withholding_account": 1,
            "dont_recompute_tax": 1,
        }

        for dim in frappe.get_all("Accounting Dimension", fields=["fieldname"]):
            if doc.get(dim.fieldname):
                row[dim.fieldname] = doc.get(dim.fieldname)

        doc.append("taxes", row)
        existing_accounts.add(account)


def recalculate_withholding_tax_amounts(doc, _method=None):
    """
    Recalculate tax_amount on every 'additional' withholding row (the ones
    apply_additional_withholding_rows is responsible for) from the current
    item totals — base = net_amount summed over items that carry that category
    in their custom_withholding_2/3 slot. If no item carries it anymore, the
    row is REMOVED.

    Rows belonging to a category used as any item's *native* tax_withholding_category
    are ERPNext's own PurchaseTaxWithholding engine's responsibility (already
    computed by the time this validate-stage hook runs) and are left untouched
    here — matched via account_category_map built only from the 'additional'
    categories set, which shouldn't overlap native accounts under normal setup
    (each category has its own dedicated account).

    Reads item.net_amount, so must run in "validate" (after ERPNext's own
    calculate_taxes_and_totals() has already run once) — same reason as
    apply_additional_withholding_rows.
    """
    taxes = doc.get("taxes") or []
    items = doc.get("items") or []
    if not taxes or not items:
        return

    categories = _get_additional_categories_from_items(doc)
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

        base = sum(
            (item.net_amount or 0)
            for item in items
            if item.get("custom_withholding_2") == cat_name or item.get("custom_withholding_3") == cat_name
        )

        tax.rate = rate

        if base == 0:
            modified = True  # drop this row — no item carries this category anymore
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


def finalize_additional_withholding_totals(doc, _method=None):
    """
    Refresh grand_total / outstanding_amount / etc. after apply_additional_withholding_rows
    and recalculate_withholding_tax_amounts have added or updated 'additional' withholding
    rows in this same validate pass. ERPNext's own controller.validate() already ran
    calculate_taxes_and_totals() once before these doc_events fired (doc_events for
    "validate" run after the doctype's own validate() method), so any row we've since
    appended/changed isn't reflected in the totals yet — mirrors what ERPNext's own
    update_tax_rows() does at its own tail end for native rows.
    """
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

def validate_additional_withholding_defaults(doc, _method=None):
    """
    Inform (non-blocking) when a header-level additional withholding default
    (custom_withholding_2/3) could not be cascaded onto any item — e.g. a
    service-only category was selected but no item on the invoice is marked
    as a service item, or a general category was selected but no item has
    Consider for Tax Withholding checked.

    By the time this validate-stage hook runs, sync_additional_withholding_categories_to_items
    has already cascaded+cleared per item, so this is purely informational —
    it never blocks the save.
    """
    if not doc.get("apply_multiple_withholding"):
        return

    count = cint(doc.get("custom_withholding_count") or 0)
    headers = []
    if count >= 1 and doc.get("custom_withholding_2"):
        headers.append(doc.get("custom_withholding_2"))
    if count >= 2 and doc.get("custom_withholding_3"):
        headers.append(doc.get("custom_withholding_3"))
    if not headers:
        return

    # A header default counts as "applied" whether it landed in an item's additional
    # slot, or turned out to already be that item's own native tax_withholding_category
    # (sync_additional_withholding_categories_to_items deliberately clears the additional
    # slot in that case to avoid double-counting the same category on one row).
    active = _get_additional_categories_from_items(doc) | _get_native_categories_from_items(doc)
    unapplied = [c for c in headers if c not in active]

    if unapplied:
        cats_html = ", ".join("<b>{}</b>".format(c) for c in unapplied)
        frappe.msgprint(
            _(
                "The following additional withholding categories did not apply to any item on "
                "this invoice: {0}. Service-only categories require at least one service item; "
                "other categories require at least one item with Consider for Tax Withholding checked."
            ).format(cats_html),
            title=_("Additional Withholding Not Applied"),
            indicator="orange",
        )


def set_withholding_tax_rates(doc, _method=None):
    """
    Set the rate field on any withholding tax row where rate is still 0 —
    covers both 'additional' category rows and ERPNext's own native-category
    rows, which populate tax_amount correctly but don't always populate the
    child row's rate field itself (Actual charge type only needs tax_amount to
    compute correctly, so core doesn't bother). Downstream reports (KRA WHTAX/
    WHVAT reports, the withholding register) back-calculate the taxable base
    from tax_amount / rate, so a blank rate there breaks that derivation even
    though the GL/tax_amount itself is fine — this backfills it either way.

    Only ever fills a blank/zero rate, never overwrites a rate already set —
    safe to run over native rows too without fighting ERPNext's own engine.
    Looks up the applicable rate from the Tax Withholding Category via
    account_head rather than parsing the description string. Runs in validate
    so the rate is persisted with the document on every save.
    """
    taxes = doc.get("taxes") or []
    categories = _get_additional_categories_from_items(doc) | _get_native_categories_from_items(doc)
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

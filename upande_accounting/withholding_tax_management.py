import frappe
from frappe import _
from frappe.utils import flt


# ---------------------------------------------------------------------------
# Withholding account resolution — shared by the Withholding Tax Register
# report and the Purchase Invoice submit/cancel hooks below.
# ---------------------------------------------------------------------------

_TAX_REPORT_TYPE_ALIASES = {
    "WHTAX": ("WHTAX", "Withholding Tax"),
    "WHVAT": ("WHVAT", "Withholding VAT"),
}


def get_withholding_accounts(company=None, report_types=("WHTAX", "WHVAT")):
    """
    Return {account_name: canonical_type} for accounts tagged as withholding
    accounts, where canonical_type is always "WHTAX" or "WHVAT" regardless of
    whether the account was tagged with the legacy ("WHTAX"/"WHVAT") or the
    newer ("Withholding Tax"/"Withholding VAT") tax_report_type label — both
    are in active use across companies.
    """
    expanded = []
    for t in report_types:
        expanded.extend(_TAX_REPORT_TYPE_ALIASES.get(t, (t,)))

    placeholders = ", ".join(["%s"] * len(expanded))
    sql = """
        SELECT name, tax_report_type
        FROM   `tabAccount`
        WHERE  account_type          = 'Tax'
          AND  is_tax_report_account  = 1
          AND  tax_report_type        IN ({ph})
          {company_cond}
    """.format(
        ph=placeholders,
        company_cond="AND company = %s" if company else "",
    )
    params = list(expanded)
    if company:
        params.append(company)

    rows = frappe.db.sql(sql, tuple(params), as_dict=True)
    return {
        r.name: ("WHVAT" if r.tax_report_type in ("WHVAT", "Withholding VAT") else "WHTAX")
        for r in rows
    }


def resolve_withholding_category(pi, account_head, company):
    """
    Resolve the Tax Withholding Category responsible for a withholding
    account on a specific invoice.

    A withholding account is often shared by several categories (different
    rates over time, goods vs. services) — resolving from the account alone
    is ambiguous. Only the categories actually selected on the invoice
    (tax_withholding_category + custom_withholding_1/2/3) are checked first;
    falls back to any category referencing the account for the company when
    none of the invoice's own categories match (legacy/manually edited data).

    `pi` may be a full Purchase Invoice Document or a dict/frappe._dict with
    the same field names — both support .get().
    """
    categories = []
    for f in ("tax_withholding_category", "custom_withholding_1", "custom_withholding_2", "custom_withholding_3"):
        val = pi.get(f)
        if val and val not in categories:
            categories.append(val)

    if categories:
        rows = frappe.get_all(
            "Tax Withholding Account",
            filters={"parent": ["in", categories], "account": account_head, "company": company},
            fields=["parent"],
        )
        matched = {r.parent for r in rows}
        for cat in categories:
            if cat in matched:
                return cat

    rows = frappe.get_all(
        "Tax Withholding Account",
        filters={"account": account_head, "company": company},
        fields=["parent"],
        limit=1,
    )
    return rows[0].parent if rows else ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_wtm_docs_for_invoice(invoice_name):
    """Return all Withholding Tax Management docs linked to a Purchase Invoice."""
    names = frappe.get_all(
        "Withholding Tax Management",
        filters={"purchase_invoice": invoice_name},
        pluck="name",
    )
    return [frappe.get_doc("Withholding Tax Management", n) for n in names]


def _upsert_payment_reference(wtm, pe_name, ref_row, pe_doc):
    """
    Add or update the WTM Payment Reference row for a Payment Entry.

    Stores transaction currency, exchange rate, per-invoice grand total and
    allocated amounts. Company-currency equivalents are stored when the
    exchange rate is not 1 (i.e. the transaction is in a foreign currency).
    """
    currency = (
        pe_doc.party_account_currency
        or frappe.db.get_value("Company", wtm.company, "default_currency")
    )
    exchange_rate = flt(ref_row.exchange_rate) or 1.0
    grand_total   = flt(ref_row.total_amount)
    allocated     = flt(ref_row.allocated_amount)

    if exchange_rate > 1:
        grand_total_base = round(grand_total * exchange_rate, 2)
        allocated_base   = round(allocated   * exchange_rate, 2)
    else:
        grand_total_base = 0.0
        allocated_base   = 0.0

    for row in wtm.payment_references or []:
        if row.reference_name == pe_name:
            row.currency                       = currency
            row.exchange_rate                  = exchange_rate
            row.grand_total                    = grand_total
            row.allocated_amount               = allocated
            row.grand_total_company_currency   = grand_total_base
            row.allocated_amount_company_currency = allocated_base
            return

    wtm.append("payment_references", {
        "reference_doctype":              "Payment Entry",
        "reference_name":                 pe_name,
        "currency":                       currency,
        "exchange_rate":                  exchange_rate,
        "grand_total":                    grand_total,
        "allocated_amount":               allocated,
        "grand_total_company_currency":   grand_total_base,
        "allocated_amount_company_currency": allocated_base,
    })


def _mark_invoice_paid_if_reconciled(wtm):
    """
    If the Purchase Invoice already has submitted Payment Entries reconciled
    against it — e.g. it was paid long before this WTM record existed, which
    is exactly the case for invoices picked up by the backfill — populate
    payment_references and tick Invoice Paid, exactly as on_payment_entry_submit
    would have done at the time of payment.

    Safe to call on a brand-new (not yet inserted) WTM doc: appends to
    payment_references in memory, the caller still needs to insert/save.
    """
    refs = frappe.get_all(
        "Payment Entry Reference",
        filters={
            "reference_doctype": "Purchase Invoice",
            "reference_name":    wtm.purchase_invoice,
            "docstatus":         1,
        },
        fields=["parent", "exchange_rate", "total_amount", "allocated_amount"],
    )
    if not refs:
        return

    for ref in refs:
        pe_doc = frappe.get_doc("Payment Entry", ref.parent)
        _upsert_payment_reference(wtm, pe_doc.name, ref, pe_doc)

    wtm.suggested_for_payment = 1


def _sync_wtm_for_payment_entry(pe_doc):
    """
    For every Purchase Invoice reference in the Payment Entry, find the
    matching Withholding Tax Management records, upsert the payment reference
    row, and mark the record as Invoice Paid.

    Fires on Payment Entry submit and on_update_after_submit (reconciliation).
    """
    for ref in pe_doc.references or []:
        if ref.reference_doctype != "Purchase Invoice":
            continue
        for wtm in _get_wtm_docs_for_invoice(ref.reference_name):
            try:
                _upsert_payment_reference(wtm, pe_doc.name, ref, pe_doc)
                wtm.suggested_for_payment = 1
                wtm.save(ignore_permissions=True)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "WTM: failed to sync payment reference for {0}".format(wtm.name),
                )


def _remove_wtm_payment_reference(pe_doc):
    """
    Remove the Payment Entry row from all linked WTM payment references tables
    when the Payment Entry is cancelled. If no payment references remain after
    removal, uncheck Invoice Paid.
    """
    for ref in pe_doc.references or []:
        if ref.reference_doctype != "Purchase Invoice":
            continue
        for wtm in _get_wtm_docs_for_invoice(ref.reference_name):
            try:
                original_len = len(wtm.payment_references or [])
                wtm.payment_references = [
                    r for r in (wtm.payment_references or [])
                    if r.reference_name != pe_doc.name
                ]
                if len(wtm.payment_references) != original_len:
                    for i, row in enumerate(wtm.payment_references):
                        row.idx = i + 1
                    if not wtm.payment_references:
                        wtm.suggested_for_payment = 0
                    wtm.save(ignore_permissions=True)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "WTM: failed to remove payment reference for {0}".format(wtm.name),
                )


# ---------------------------------------------------------------------------
# Payment Entry doc event hooks
# ---------------------------------------------------------------------------

def on_payment_entry_submit(doc, method=None):
    _sync_wtm_for_payment_entry(doc)


def on_payment_entry_update_after_submit(doc, method=None):
    # Fires when a submitted PE is saved — covers payment reconciliation
    _sync_wtm_for_payment_entry(doc)


def on_payment_entry_cancel(doc, method=None):
    _remove_wtm_payment_reference(doc)


def on_payment_entry_trash(doc, method=None):
    _remove_wtm_payment_reference(doc)


# ---------------------------------------------------------------------------
# Purchase Invoice doc event hooks — create/remove WTM records
# ---------------------------------------------------------------------------

def create_wtm_records_for_invoice(doc, method=None):
    """
    Called from hooks.py on Purchase Invoice submit.
    Creates a Withholding Tax Management record for each withholding tax
    line on the invoice, skipping any (invoice, account) pair that already
    has one — safe to call again from backfill_withholding_tax_management.

    For a pair that already has a record but isn't yet marked Invoice Paid,
    re-checks reconciliation instead of skipping outright — covers records
    created by an earlier backfill run before the invoice's payment was
    reconciled (or before this check existed).
    """
    wh_accounts = get_withholding_accounts(doc.company)
    if not wh_accounts:
        return

    lines = frappe.db.sql("""
        SELECT account_head, base_tax_amount_after_discount_amount
        FROM   `tabPurchase Taxes and Charges`
        WHERE  parent      = %s
          AND  account_head IN ({ph})
          AND  tax_amount  > 0
    """.format(ph=", ".join(["%s"] * len(wh_accounts))),
        tuple([doc.name] + list(wh_accounts.keys())),
        as_dict=True,
    )

    for line in lines:
        existing_name = frappe.db.exists("Withholding Tax Management", {
            "purchase_invoice":   doc.name,
            "withholding_account": line.account_head,
        })
        if existing_name:
            try:
                existing_wtm = frappe.get_doc("Withholding Tax Management", existing_name)
                if not existing_wtm.suggested_for_payment:
                    _mark_invoice_paid_if_reconciled(existing_wtm)
                    if existing_wtm.suggested_for_payment:
                        existing_wtm.save(ignore_permissions=True)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "Failed to refresh Invoice Paid status for {0}".format(existing_name),
                )
            continue
        try:
            wtm = frappe.new_doc("Withholding Tax Management")
            wtm.purchase_invoice     = doc.name
            wtm.withholding_account  = line.account_head
            wtm.supplier             = doc.supplier
            wtm.withheld_amount      = flt(line.base_tax_amount_after_discount_amount, 2)
            wtm.payment_status       = "Unpaid"
            wtm.withholding_category = resolve_withholding_category(doc, line.account_head, doc.company)
            _mark_invoice_paid_if_reconciled(wtm)
            wtm.insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Failed to create Withholding Tax Management on submit: {0}".format(doc.name),
            )


def cancel_wtm_records_for_invoice(doc, method=None):
    """
    Called from hooks.py on Purchase Invoice cancel.
    1. Cancels any submitted Withholding Payment Entries that reference the
       invoice's WTM records (reverses the KRA remittance journal entry).
    2. Deletes all WTM records for the invoice, regardless of payment status.
    """
    wtm_names = frappe.get_all(
        "Withholding Tax Management",
        filters={"purchase_invoice": doc.name},
        pluck="name",
    )
    if not wtm_names:
        return

    wpe_names = set(frappe.get_all(
        "WPE Withholding Entry",
        filters={"wtm_reference": ["in", wtm_names]},
        pluck="parent",
    ))
    for wpe_name in wpe_names:
        try:
            wpe = frappe.get_doc("Withholding Payment Entry", wpe_name)
            if wpe.docstatus == 1:
                wpe.cancel()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Failed to cancel WPE on invoice cancel: {0}".format(wpe_name),
            )

    for name in wtm_names:
        try:
            frappe.delete_doc("Withholding Tax Management", name, ignore_permissions=True, force=True)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "Failed to delete Withholding Tax Management on cancel: {0}".format(name),
            )


# ---------------------------------------------------------------------------
# Backfill — for invoices submitted while the on_submit hook was disabled
# ---------------------------------------------------------------------------

@frappe.whitelist()
def backfill_withholding_tax_management(company=None, from_date=None, to_date=None, supplier=None):
    """
    Create missing Withholding Tax Management records for already-submitted
    Purchase Invoices that have a withholding tax line but no WTM record yet
    — e.g. invoices submitted while the on_submit hook was disabled.

    Safe to run repeatedly: create_wtm_records_for_invoice() skips any
    (invoice, account) pair that already has a record.
    """
    wh_accounts = get_withholding_accounts(company)
    if not wh_accounts:
        return {
            "status": "error",
            "message": _("No accounts are tagged as WHTAX or WHVAT for this company."),
        }

    conditions = ["pi.docstatus = 1"]
    params = []
    if company:
        conditions.append("pi.company = %s")
        params.append(company)
    if from_date:
        conditions.append("pi.posting_date >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("pi.posting_date <= %s")
        params.append(to_date)
    if supplier:
        conditions.append("pi.supplier = %s")
        params.append(supplier)

    acc_ph = ", ".join(["%s"] * len(wh_accounts))
    invoices = frappe.db.sql("""
        SELECT DISTINCT pi.name AS invoice_number
        FROM `tabPurchase Invoice` pi
        JOIN `tabPurchase Taxes and Charges` pit
            ON  pit.parent       = pi.name
            AND pit.account_head IN ({acc_ph})
            AND pit.tax_amount   > 0
        WHERE {conditions}
    """.format(acc_ph=acc_ph, conditions=" AND ".join(conditions)),
        tuple(list(wh_accounts.keys()) + params),
        as_dict=True,
    )

    created = 0
    failed  = []
    for row in invoices:
        try:
            before = frappe.db.count("Withholding Tax Management", {"purchase_invoice": row.invoice_number})
            doc = frappe.get_doc("Purchase Invoice", row.invoice_number)
            create_wtm_records_for_invoice(doc)
            after = frappe.db.count("Withholding Tax Management", {"purchase_invoice": row.invoice_number})
            created += after - before
        except Exception:
            failed.append(row.invoice_number)
            frappe.log_error(
                frappe.get_traceback(),
                "WTM backfill failed for {0}".format(row.invoice_number),
            )

    frappe.db.commit()

    message = _("Scanned {0} invoice(s), created {1} new Withholding Tax Management record(s).").format(
        len(invoices), created
    )
    if failed:
        message += " " + _("{0} invoice(s) failed — see Error Log.").format(len(failed))

    return {
        "status":           "success",
        "invoices_scanned": len(invoices),
        "records_created":  created,
        "failed_invoices":  failed,
        "message":          message,
    }


# ---------------------------------------------------------------------------
# Whitelisted API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def unreconcile_payment(wtm_name, row_name):
    """
    Remove a single payment reference row from a Withholding Tax Management
    record. Called from the form's Unreconcile Payment button.
    If no references remain after removal, uncheck Invoice Paid.
    """
    wtm = frappe.get_doc("Withholding Tax Management", wtm_name)

    original_len = len(wtm.payment_references or [])
    wtm.payment_references = [
        r for r in (wtm.payment_references or [])
        if r.name != row_name
    ]

    if len(wtm.payment_references) == original_len:
        frappe.throw(_("Payment reference row not found: {0}").format(row_name))

    for i, row in enumerate(wtm.payment_references):
        row.idx = i + 1

    if not wtm.payment_references:
        wtm.suggested_for_payment = 0

    wtm.save(ignore_permissions=True)
    frappe.db.commit()
    return {"status": "success"}

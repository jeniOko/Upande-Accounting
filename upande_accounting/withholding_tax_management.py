import frappe
from frappe import _
from frappe.utils import flt


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

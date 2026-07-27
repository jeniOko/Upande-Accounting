# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Withholding VAT KRA Report
==========================
KRA-compatible Withholding VAT filing summary.

Only includes Purchase Invoices where the linked Withholding Tax Management
record has payment_status = 'Paid', meaning the withheld VAT has already
been remitted to KRA.

The From Date / To Date filters apply to wtm.payment_date (the remittance
date), not the invoice's posting/bill date — this is a filing report, so the
relevant period is when the withholding was paid over to KRA.

On-screen column order:
  PIN | Invoice Number | Invoice Date | Taxable Amount |
  WHT VAT Rate (%) | WHT VAT Amount | Payment Date | PRN Number

The CSV/XLSX download only goes up to Taxable Amount (PIN | Supplier Name |
Invoice Number | Invoice Date | Taxable Amount) — Rate/Tax Amount/Payment Date
are on-screen only, for verification, not part of the KRA upload file.

Accounts resolved via is_tax_report_account + tax_report_type IN
('Withholding VAT', 'WHVAT') on the Account master.

Taxable Amount = tax_amount / (rate / 100) per invoice tax row — the base
specific to THIS category's rate, not the whole invoice's gross value, since
only the items actually subject to that category should count.

The join to Withholding Tax Management pins to a single deterministic row
(via a LIMIT 1 subquery) rather than joining loosely on
(purchase_invoice, withholding_account, payment_status='Paid'), so more than
one Paid WTM row for the same invoice/account can never fan this query out
into duplicate result rows.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)
    columns = get_columns()
    data = get_data(filters)
    message = (
        '<div style="padding:8px 12px; background:#e8f5e9; border-left:4px solid #43a047; '
        'border-radius:3px; color:#1b5e20;">'
        '<b>Paid Records Only</b> &mdash; This report shows invoices where the '
        'Withholding Tax Management record is marked <em>Paid</em> (remitted to KRA).'
        '</div>'
    )
    return columns, data, message


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_filters(filters):
    if not filters.get("company"):
        frappe.throw(_("Please select a Company."))
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Please set both From Date and To Date."))


# ---------------------------------------------------------------------------
# Columns — KRA Withholding VAT upload format
# ---------------------------------------------------------------------------

def get_columns():
    return [
        {
            "label":     _("PIN"),
            "fieldname": "tax_id",
            "fieldtype": "Data",
            "width":     150,
        },
        {
            "label":     _("Supplier Name"),
            "fieldname": "supplier_name",
            "fieldtype": "Data",
            "width":     220,
        },
        {
            "label":     _("Invoice Number"),
            "fieldname": "bill_no",
            "fieldtype": "Data",
            "width":     160,
        },
        {
            "label":     _("Invoice Date"),
            "fieldname": "bill_date",
            "fieldtype": "Date",
            "width":     110,
        },
        {
            "label":     _("Taxable Amount (KES)"),
            "fieldname": "taxable_amount",
            "fieldtype": "Currency",
            "width":     160,
        },
        {
            "label":     _("WHT VAT Rate (%)"),
            "fieldname": "tax_rate",
            "fieldtype": "Float",
            "precision": 2,
            "width":     110,
        },
        {
            "label":     _("WHT VAT Amount (KES)"),
            "fieldname": "tax_amount",
            "fieldtype": "Currency",
            "width":     160,
        },
        {
            "label":     _("Withholding Payment Date"),
            "fieldname": "payment_date",
            "fieldtype": "Date",
            "width":     160,
        },
        {
            "label":     _("System Invoice No"),
            "fieldname": "invoice_number",
            "fieldtype": "Link",
            "options":   "Purchase Invoice",
            "width":     160,
        },
    ]


# ---------------------------------------------------------------------------
# Account resolution — WHVAT accounts only
# ---------------------------------------------------------------------------

def get_whvat_accounts(company):
    """Return account names tagged as Withholding VAT for the company."""
    sql = """
        SELECT name
        FROM   `tabAccount`
        WHERE  account_type          = 'Tax'
          AND  is_tax_report_account  = 1
          AND  tax_report_type        IN ('Withholding VAT', 'WHVAT')
          {company_cond}
    """.format(company_cond="AND company = %s" if company else "")
    params = (company,) if company else ()
    rows = frappe.db.sql(sql, params, as_dict=True)
    return [r.name for r in rows]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters):
    company = filters.get("company")

    accounts = get_whvat_accounts(company)
    if not accounts:
        frappe.msgprint(
            _(
                "No accounts are tagged as Withholding VAT. "
                "Open the relevant Tax accounts, enable <b>Include in Tax Report</b> "
                "and set <b>Tax Report Type</b> to <b>Withholding VAT</b>."
            ),
            indicator="orange",
            title=_("No WHVAT Accounts Found"),
        )
        return []

    if filters.get("withholding_account"):
        acct = filters["withholding_account"]
        if acct in accounts:
            accounts = [acct]
        else:
            frappe.msgprint(
                _("The selected account is not tagged as Withholding VAT."),
                indicator="orange",
            )
            return []

    acc_ph = ", ".join(["%s"] * len(accounts))
    conditions, params = build_conditions(filters)

    sql = """
        SELECT
            pi.name                                             AS invoice_number,
            pi.bill_no,
            pi.bill_date,
            pi.supplier,
            pi.supplier_name,
            sup.tax_id,
            pit.account_head                                    AS withholding_account,
            pit.base_tax_amount_after_discount_amount           AS tax_amount,
            pit.rate                                            AS tax_rate,
            CASE
                WHEN pit.rate > 0
                THEN ROUND(pit.base_tax_amount_after_discount_amount * 100.0 / pit.rate, 2)
                ELSE NULL
            END                                                 AS taxable_amount,
            wtm.payment_date,
            wtm.prn_number,
            wtm.name                                            AS wtm_name
        FROM `tabPurchase Invoice` pi
        JOIN `tabPurchase Taxes and Charges` pit
            ON  pit.parent       = pi.name
            AND pit.account_head IN ({acc_ph})
            AND pit.tax_amount   > 0
        JOIN `tabWithholding Tax Management` wtm
            ON  wtm.name = (
                SELECT wtm2.name
                FROM   `tabWithholding Tax Management` wtm2
                WHERE  wtm2.purchase_invoice    = pi.name
                  AND  wtm2.withholding_account = pit.account_head
                  AND  wtm2.payment_status      = 'Paid'
                ORDER BY wtm2.name
                LIMIT  1
            )
        LEFT JOIN `tabSupplier` sup
            ON  sup.name = pi.supplier
        WHERE pi.docstatus = 1
        {conditions}
        ORDER BY wtm.payment_date ASC, pi.supplier ASC
    """.format(acc_ph=acc_ph, conditions=conditions)

    rows = frappe.db.sql(sql, tuple(accounts + params), as_dict=True)

    result = []
    for row in rows:
        result.append({
            "tax_id":        row.get("tax_id") or "",
            "supplier_name": row.get("supplier_name") or row.get("supplier") or "",
            "bill_no":       row.get("bill_no") or row.get("invoice_number") or "",
            "bill_date":     row.get("bill_date"),
            "taxable_amount": flt(row.get("taxable_amount")),
            "tax_rate":       flt(row.get("tax_rate"), 2),
            "tax_amount":     flt(row.get("tax_amount")),
            "payment_date":   row.get("payment_date"),
            "invoice_number": row.get("invoice_number"),
        })

    return result


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def build_conditions(filters):
    """
    from_date/to_date filter on wtm.payment_date, not the invoice's own date —
    this is a KRA remittance filing report (only Paid WTM records are shown at
    all), so the relevant period is when the withholding was actually paid
    over to KRA, not when the underlying purchase invoice was raised.
    """
    conditions = []
    params = []

    if filters.get("company"):
        conditions.append("pi.company = %s")
        params.append(filters["company"])

    if filters.get("from_date"):
        conditions.append("wtm.payment_date >= %s")
        params.append(filters["from_date"])

    if filters.get("to_date"):
        conditions.append("wtm.payment_date <= %s")
        params.append(filters["to_date"])

    if filters.get("supplier"):
        conditions.append("pi.supplier = %s")
        params.append(filters["supplier"])

    cond_str = ("AND " + " AND ".join(conditions)) if conditions else ""
    return cond_str, params


# ---------------------------------------------------------------------------
# XLSX download — generated server-side (frappe.utils.xlsxutils), same as
# Frappe's own report Excel export; no client-side XLSX library involved.
#
# Column set stops at Taxable Amount (PIN, Supplier Name, Invoice Number,
# Invoice Date, Taxable Amount) — Rate/Tax Amount/Payment Date are shown on
# screen for verification but aren't part of the download.
# ---------------------------------------------------------------------------

def _download_field_map():
    return (
        ["PIN", "Supplier Name", "Invoice Number", "Invoice Date", "Taxable Amount (KES)"],
        ["tax_id", "supplier_name", "bill_no", "bill_date", "taxable_amount"],
    )


@frappe.whitelist()
def download_xlsx(filters=None):
    import json

    from frappe.utils.xlsxutils import build_xlsx_response

    if isinstance(filters, str):
        filters = json.loads(filters)
    filters = filters or {}
    validate_filters(filters)

    rows = get_data(filters)
    headers, field_map = _download_field_map()

    data = [headers] + [[row.get(f) if row.get(f) is not None else "" for f in field_map] for row in rows]

    from_date = filters.get("from_date") or ""
    to_date = filters.get("to_date") or ""
    build_xlsx_response(data, "Withholding_VAT_KRA_{0}_to_{1}".format(from_date, to_date))

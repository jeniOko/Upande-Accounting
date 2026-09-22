# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Withholding VAT KRA Report
==========================
KRA-compatible Withholding VAT filing summary.

Only includes Purchase Invoices where the linked Withholding Tax Management
record has suggested_for_payment = 1, i.e. the invoice itself has actually
been paid to the supplier (a Payment Entry has been submitted against it) —
that's when VAT is withheld in the first place, regardless of Frappe's own
`Purchase Invoice.status` label.

By default only rows with payment_status = 'Unpaid' are shown (withheld VAT
that has NOT yet been remitted to KRA) — the actionable "still owed" view.
The "Remittance Status" filter can be switched to 'Paid' (already remitted)
or 'All'.

The From Date / To Date filters apply to wtm.payment_date (the remittance
date) when present; since Unpaid rows have no payment_date yet, they fall
back to the invoice's bill_date / posting_date so they aren't silently
dropped from every date-filtered result.

On-screen column order:
  PIN | Invoice Number | Invoice Date | Taxable Amount |
  WHT VAT Rate (%) | WHT VAT Amount | Remittance Status | Payment Date | PRN Number

The CSV/XLSX download only goes up to Taxable Amount (PIN | Supplier Name |
Invoice Number | Invoice Date | Taxable Amount) — Rate/Tax Amount/Remittance
Status/Payment Date are on-screen only, for verification, not part of the
KRA upload file.

Accounts resolved via is_tax_report_account + tax_report_type IN
('Withholding VAT', 'WHVAT') on the Account master.

Taxable Amount = tax_amount / (rate / 100) per invoice tax row — the base
specific to THIS category's rate, not the whole invoice's gross value, since
only the items actually subject to that category should count.

The join to Withholding Tax Management pins to a single deterministic row
(via a LIMIT 1 subquery) rather than joining loosely on
(purchase_invoice, withholding_account, suggested_for_payment=1), so more
than one matching WTM row for the same invoice/account can never fan this
query out into duplicate result rows.
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)
    columns = get_columns()
    data = get_data(filters)
    remittance_status = (filters.get("remittance_status") or "Unpaid").strip()
    if remittance_status == "All":
        message = (
            '<div style="padding:8px 12px; background:#e8f5e9; border-left:4px solid #43a047; '
            'border-radius:3px; color:#1b5e20;">'
            '<b>All Remittance Statuses</b> &mdash; Showing invoices with withheld VAT that has '
            'been paid to the supplier, whether or not it has been remitted to KRA yet.'
            '</div>'
        )
    elif remittance_status == "Paid":
        message = (
            '<div style="padding:8px 12px; background:#e8f5e9; border-left:4px solid #43a047; '
            'border-radius:3px; color:#1b5e20;">'
            '<b>Remitted Only</b> &mdash; This report is showing invoices where the withheld VAT '
            'has already been <em>remitted to KRA</em>. Switch <em>Remittance Status</em> to '
            '<em>Unpaid</em> to see VAT still owed to KRA.'
            '</div>'
        )
    else:
        message = (
            '<div style="padding:8px 12px; background:#e8f4fd; border-left:4px solid #2196f3; '
            'border-radius:3px; color:#1a5276;">'
            '<b>Pending Remittance Only</b> &mdash; This report is showing invoices that have '
            'been paid to the supplier but whose withheld VAT has <em>not yet</em> been remitted '
            'to KRA. Switch <em>Remittance Status</em> to <em>Paid</em> or <em>All</em> to see other records.'
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
            "label":     _("Remittance Status"),
            "fieldname": "payment_status",
            "fieldtype": "Data",
            "width":     120,
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

    remittance_status = (filters.get("remittance_status") or "Unpaid").strip()
    wtm_params = []
    if remittance_status in ("Paid", "Unpaid"):
        remittance_cond = "AND wtm2.payment_status = %s"
        wtm_params.append(remittance_status)
    else:
        remittance_cond = ""

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
            wtm.payment_status,
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
                WHERE  wtm2.purchase_invoice     = pi.name
                  AND  wtm2.withholding_account  = pit.account_head
                  AND  wtm2.suggested_for_payment = 1
                  {remittance_cond}
                ORDER BY wtm2.name
                LIMIT  1
            )
        LEFT JOIN `tabSupplier` sup
            ON  sup.name = pi.supplier
        WHERE pi.docstatus = 1
        {conditions}
        ORDER BY COALESCE(wtm.payment_date, pi.bill_date, pi.posting_date) ASC, pi.supplier ASC
    """.format(acc_ph=acc_ph, remittance_cond=remittance_cond, conditions=conditions)

    rows = frappe.db.sql(sql, tuple(accounts + wtm_params + params), as_dict=True)

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
            "payment_status": row.get("payment_status") or "Unpaid",
            "payment_date":   row.get("payment_date"),
            "invoice_number": row.get("invoice_number"),
        })

    return result


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def build_conditions(filters):
    """
    from_date/to_date filter on wtm.payment_date (the remittance date) when
    the invoice has one; unpaid remittances (wtm.payment_date is NULL, shown
    whenever Remittance Status isn't restricted to 'Paid') fall back to the
    invoice's bill_date / posting_date so they aren't silently dropped from
    every date-filtered result.
    """
    conditions = []
    params = []

    if filters.get("company"):
        conditions.append("pi.company = %s")
        params.append(filters["company"])

    date_expr = "COALESCE(wtm.payment_date, pi.bill_date, pi.posting_date)"

    if filters.get("from_date"):
        conditions.append("{0} >= %s".format(date_expr))
        params.append(filters["from_date"])

    if filters.get("to_date"):
        conditions.append("{0} <= %s".format(date_expr))
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

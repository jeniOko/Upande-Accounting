# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe


"""
Produces a KRA-compatible withholding tax (WHTAX) filing summary with columns:
  Nature of Transaction | Country | Residential Status | Date of Payment |
  PIN | Supplier Name | Invoice Number | Email Address |
  Gross Amount | Rate | Tax Amount

All submitted invoices with a withholding tax line are included (paid and unpaid).
Payment date is shown from the linked Withholding Tax Management record (null if unpaid).

From Date / To Date filter on wtm.payment_date (the remittance date) where the
invoice has one; unpaid invoices fall back to the invoice's own posting_date
so they still appear when Paid Invoices Only is unchecked.

Accounts resolved via is_tax_report_account + tax_report_type = "Withholding Tax".
Nature of Transaction resolved per tax row via:
  pit.account_head → tabTax Withholding Account → tabTax Withholding Category.nature_of_transaction
  This is account-based, not category-field-based, so it works regardless of whether
  the category came from an item's native tax_withholding_category or from our own
  additional-withholding item fields.
Gross Amount = Purchase Invoice.gross_amount (net_total + taxes_and_charges_added,
i.e. all tax rows with add_deduct_tax = "Add" — this app's own pre-existing definition,
set on before_save via upande_accounting.utils.set_gross_amount). It is a whole-invoice
figure, not apportioned per withholding row — every withholding row on the same
invoice shows the same Gross Amount.
Residential Status derived from supplier country (Kenya = Resident, else Non Resident).
"""


import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)
    columns = get_columns()
    data    = get_data(filters)
    message = None
    if filters.get("paid_only", 1):
        message = (
            '<div style="padding:8px 12px; background:#e8f4fd; border-left:4px solid #2196f3; '
            'border-radius:3px; color:#1a5276;">'
            '<b>Paid Invoices Only</b> &mdash; This report is showing only fully paid invoices '
            'that have withholding tax. Uncheck <em>Paid Invoices Only</em> to include all '
            'submitted invoices regardless of payment status.'
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
# Columns — matching KRA upload format
# ---------------------------------------------------------------------------

def get_columns():
    return [
        {
            "label":     _("Nature of Transaction"),
            "fieldname": "nature_of_transaction",
            "fieldtype": "Data",
            "width":     260,
        },
        {
            "label":     _("Country"),
            "fieldname": "country",
            "fieldtype": "Data",
            "width":     120,
        },
        {
            "label":     _("Residential Status"),
            "fieldname": "residential_status",
            "fieldtype": "Data",
            "width":     130,
        },
        {
            "label":     _("Date of Payment"),
            "fieldname": "payment_date",
            "fieldtype": "Date",
            "width":     120,
        },
        {
            "label":     _("PIN"),
            "fieldname": "tax_id",
            "fieldtype": "Data",
            "width":     140,
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
            "label":     _("Email Address"),
            "fieldname": "email",
            "fieldtype": "Data",
            "width":     180,
        },
        {
            "label":     _("Gross Amount"),
            "fieldname": "gross_amount",
            "fieldtype": "Currency",
            "width":     140,
        },
        {
            "label":     _("Rate (%)"),
            "fieldname": "tax_rate",
            "fieldtype": "Float",
            "precision": 2,
            "width":     90,
        },
        {
            "label":     _("Tax Amount"),
            "fieldname": "tax_amount",
            "fieldtype": "Currency",
            "width":     140,
        },
    ]


# ---------------------------------------------------------------------------
# Account resolution (shared with register)
# ---------------------------------------------------------------------------

def get_withholding_accounts(company):
    # Accept both the new "Withholding Tax" label and the legacy "WHTAX" value
    # so existing accounts don't need to be manually updated.
    sql = """
        SELECT name
        FROM   `tabAccount`
        WHERE  account_type          = 'Tax'
          AND  is_tax_report_account  = 1
          AND  tax_report_type        IN ('Withholding Tax', 'WHTAX')
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

    accounts = get_withholding_accounts(company)
    if not accounts:
        frappe.msgprint(
            _("No accounts tagged as Withholding Tax. "
              "Tag accounts via Account → Include in Tax Report → Withholding Tax."),
            indicator="orange",
        )
        return []

    if filters.get("withholding_account"):
        acct = filters["withholding_account"]
        if acct in accounts:
            accounts = [acct]
        else:
            frappe.msgprint(_("Selected account is not tagged as Withholding Tax."), indicator="orange")
            return []

    acc_ph = ", ".join(["%s"] * len(accounts))
    conditions, params = build_conditions(filters)

    sql = """
        SELECT
            pi.name                                         AS invoice_number,
            pi.bill_no,
            pi.posting_date,
            pi.supplier,
            pi.supplier_name,
            sup.tax_id,
            sup.country,
            (
                SELECT c.email_id
                FROM   `tabContact` c
                JOIN   `tabDynamic Link` dl
                       ON  dl.parent       = c.name
                       AND dl.link_doctype = 'Supplier'
                       AND dl.link_name    = pi.supplier
                WHERE  c.email_id IS NOT NULL
                  AND  c.email_id != ''
                ORDER BY c.is_primary_contact DESC, c.creation ASC
                LIMIT  1
            )                                               AS email,
            pit.account_head                                AS withholding_account,
            pit.base_tax_amount_after_discount_amount       AS tax_amount,
            pit.rate                                        AS tax_rate,
            pi.gross_amount                                 AS gross_amount,
            twcat.nature_of_transaction,
            wtm.payment_date,
            wtm.prn_number
        FROM `tabPurchase Invoice` pi
        JOIN `tabPurchase Taxes and Charges` pit
            ON  pit.parent       = pi.name
            AND pit.account_head IN ({acc_ph})
            AND pit.tax_amount   > 0
        LEFT JOIN `tabTax Withholding Account` twa
            ON  twa.account  = pit.account_head
            AND twa.company  = pi.company
        LEFT JOIN `tabTax Withholding Category` twcat
            ON  twcat.name   = twa.parent
        LEFT JOIN `tabWithholding Tax Management` wtm
            ON  wtm.purchase_invoice    = pi.name
            AND wtm.withholding_account = pit.account_head
        LEFT JOIN `tabSupplier` sup ON sup.name = pi.supplier
        WHERE pi.docstatus = 1
        {conditions}
        ORDER BY COALESCE(wtm.payment_date, pi.posting_date) ASC, pi.supplier ASC
    """.format(acc_ph=acc_ph, conditions=conditions)

    rows = frappe.db.sql(sql, tuple(accounts + params), as_dict=True)

    result = []
    for row in rows:
        country = (row.get("country") or "").strip()
        result.append({
            "nature_of_transaction": row.get("nature_of_transaction") or "Other Income",
            "country":               country or "Kenya",
            "residential_status":    "Resident" if country.lower() == "kenya" else "Non Resident",
            "payment_date":          row.get("payment_date"),
            "tax_id":                row.get("tax_id") or "",
            "supplier_name":         row.get("supplier_name") or row.get("supplier") or "",
            "bill_no":               row.get("bill_no") or row.get("invoice_number") or "",
            "email":                 row.get("email") or "",
            "gross_amount":          flt(row.get("gross_amount")),
            "tax_rate":              flt(row.get("tax_rate"), 2),
            "tax_amount":            flt(row.get("tax_amount")),
            "_invoice_number":       row.get("invoice_number"),
            "_prn_number":           row.get("prn_number"),
        })

    return result


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def build_conditions(filters):
    """
    from_date/to_date filter on wtm.payment_date (the remittance date) when
    the invoice has one; unpaid invoices (wtm.payment_date is NULL, shown when
    paid_only is unchecked) fall back to the invoice's own posting_date so
    they aren't silently dropped from every date-filtered result.
    """
    conditions = []
    params     = []

    if filters.get("company"):
        conditions.append("pi.company = %s")
        params.append(filters["company"])

    if filters.get("from_date"):
        conditions.append("COALESCE(wtm.payment_date, pi.posting_date) >= %s")
        params.append(filters["from_date"])

    if filters.get("to_date"):
        conditions.append("COALESCE(wtm.payment_date, pi.posting_date) <= %s")
        params.append(filters["to_date"])

    if filters.get("supplier"):
        conditions.append("pi.supplier = %s")
        params.append(filters["supplier"])

    if filters.get("paid_only", 1):
        conditions.append("pi.status = 'Paid'")

    cond_str = ("AND " + " AND ".join(conditions)) if conditions else ""
    return cond_str, params


# ---------------------------------------------------------------------------
# XLSX download — generated server-side (frappe.utils.xlsxutils), same as
# Frappe's own report Excel export. The client no longer needs a bundled
# SheetJS/XLSX library, which was never actually available in the desk
# frontend to begin with.
# ---------------------------------------------------------------------------

@frappe.whitelist()
def download_xlsx(filters=None):
    import json

    from frappe.utils.xlsxutils import build_xlsx_response

    if isinstance(filters, str):
        filters = json.loads(filters)
    filters = filters or {}
    validate_filters(filters)

    rows = get_data(filters)

    headers = [
        "Nature of Transaction", "Country", "Residential Status", "Date of Payment",
        "PIN", "Supplier Name", "Invoice Number", "Email Address",
        "Gross Amount", "Rate", "Tax Amount",
    ]
    field_map = [
        "nature_of_transaction", "country", "residential_status", "payment_date",
        "tax_id", "supplier_name", "bill_no", "email",
        "gross_amount", "tax_rate", "tax_amount",
    ]

    data = [headers] + [[row.get(f) if row.get(f) is not None else "" for f in field_map] for row in rows]

    from_date = filters.get("from_date") or ""
    to_date = filters.get("to_date") or ""
    build_xlsx_response(data, "Withholding_Tax_KRA_{0}_to_{1}".format(from_date, to_date))
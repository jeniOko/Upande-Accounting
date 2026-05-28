# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe


"""

Produces a KRA-compatible withholding tax filing summary with columns:
  Nature of Transaction | Country | Residential Status | Date of Payment |
  PIN | Supplier Name | Invoice Number | Email Address |
  Gross Amount | Rate | Tax Amount

Covers both WHTAX and WHVAT (user selects via filter).
Only PAID withholding entries are included — unpaid ones have no payment date.

Accounts resolved via is_tax_report_account + tax_report_type tags.
Nature of Transaction resolved from Tax Withholding Category custom field.
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
    return columns, data


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_filters(filters):
    if not filters.get("company"):
        frappe.throw(_("Please select a Company."))
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Please set both From Date and To Date."))
    if not filters.get("withholding_type"):
        frappe.throw(_("Please select a Withholding Type (WHTAX or WHVAT)."))


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

def get_withholding_accounts(company, report_type):
    sql = """
        SELECT name
        FROM   `tabAccount`
        WHERE  account_type         = 'Tax'
          AND  is_tax_report_account = 1
          AND  tax_report_type       = %s
          {company_cond}
    """.format(company_cond="AND company = %s" if company else "")
    params = [report_type]
    if company:
        params.append(company)
    rows = frappe.db.sql(sql, tuple(params), as_dict=True)
    return [r.name for r in rows]


def get_nature_map():
    rows = frappe.get_all(
        "Tax Withholding Category",
        fields=["name", "nature_of_transaction"],
    )
    return {r.name: r.nature_of_transaction or "" for r in rows}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters):
    company      = filters.get("company")
    report_type  = filters["withholding_type"]    # WHTAX or WHVAT

    accounts = get_withholding_accounts(company, report_type)
    if not accounts:
        frappe.msgprint(
            _("No accounts tagged as {0}. Tag accounts via Account → Include in Tax Report → {0}.").format(report_type),
            indicator="orange",
        )
        return []

    # Optional single-account filter
    if filters.get("withholding_account"):
        acct = filters["withholding_account"]
        if acct in accounts:
            accounts = [acct]
        else:
            frappe.msgprint(_("Selected account is not tagged as {0}.").format(report_type), indicator="orange")
            return []

    nature_map   = get_nature_map()
    acc_ph       = ", ".join(["%s"] * len(accounts))
    conditions, params = build_conditions(filters)

    sql = """
        SELECT
            pi.name                                         AS invoice_number,
            pi.bill_no,
            pi.bill_date,
            pi.supplier,
            pi.supplier_name,
            pi.tax_withholding_category,
            pi.base_tax_withholding_net_total               AS gross_amount,
            pi.currency                                     AS transaction_currency,
            pi.conversion_rate                              AS exchange_rate,
            sup.tax_id,
            sup.country,
            -- Email: pulled from the primary Contact linked to the supplier
            (
                SELECT c.email_id
                FROM   `tabContact` c
                JOIN   `tabDynamic Link` dl
                       ON  dl.parent     = c.name
                       AND dl.link_doctype = 'Supplier'
                       AND dl.link_name   = pi.supplier
                WHERE  c.email_id IS NOT NULL
                  AND  c.email_id != ''
                ORDER BY c.is_primary_contact DESC, c.creation ASC
                LIMIT  1
            )                                               AS email,
            pit.account_head                                AS withholding_account,
            pit.base_tax_amount_after_discount_amount       AS tax_amount,
            pit.rate                                        AS tax_rate,
            wtp.payment_date,
            wtp.prn_number
        FROM `tabPurchase Invoice` pi
        JOIN `tabPurchase Taxes and Charges` pit
            ON  pit.parent      = pi.name
            AND pit.account_head IN ({acc_ph})
            AND pit.tax_amount  > 0
        JOIN `tabWithholding Tax Payment` wtp
            ON  wtp.purchase_invoice    = pi.name
            AND wtp.withholding_account = pit.account_head
            AND wtp.payment_status      = 'Paid'
        LEFT JOIN `tabSupplier` sup ON sup.name = pi.supplier
        WHERE pi.docstatus = 1
        {conditions}
        ORDER BY wtp.payment_date ASC, pi.supplier ASC
    """.format(acc_ph=acc_ph, conditions=conditions)

    all_params = accounts + params
    rows = frappe.db.sql(sql, tuple(all_params), as_dict=True)

    result = []
    for row in rows:
        country = (row.get("country") or "").strip()
        twc     = row.get("tax_withholding_category") or ""
        result.append({
            "nature_of_transaction": nature_map.get(twc, "Other Income"),
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
            # Keep for reference but not shown as column
            "_invoice_number":       row.get("invoice_number"),
            "_prn_number":           row.get("prn_number"),
        })

    return result


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def build_conditions(filters):
    conditions = []
    params     = []

    if filters.get("company"):
        conditions.append("pi.company = %s")
        params.append(filters["company"])

    if filters.get("from_date"):
        conditions.append("wtp.payment_date >= %s")
        params.append(filters["from_date"])

    if filters.get("to_date"):
        conditions.append("wtp.payment_date <= %s")
        params.append(filters["to_date"])

    if filters.get("supplier"):
        conditions.append("pi.supplier = %s")
        params.append(filters["supplier"])

    cond_str = ("AND " + " AND ".join(conditions)) if conditions else ""
    return cond_str, params
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

Accounts resolved via is_tax_report_account + tax_report_type = "Withholding Tax".

Nature of Transaction / rate / gross amount are resolved per invoice in Python
(not via a SQL join against Tax Withholding Account) because a single withholding
account is commonly shared by several Tax Withholding Categories (e.g. different
rates over time, or goods vs. services). Joining on the account alone fans a
single tax row out into one row per matching category — the same invoice then
appears multiple times with the same amount. Instead, only the categories
actually selected on the invoice itself (tax_withholding_category plus
custom_withholding_1/2/3) are considered, exactly mirroring the resolution
logic in upande_accounting.utils that calculated the withholding in the first
place.

Gross amount = the actual item-level base the rate was applied to, not the
invoice's overall gross/net total:
  - Service-only categories → sum of net_amount for items flagged
    custom_is_service_item=1.
  - All-item categories      → sum of net_amount for items flagged apply_tds=1.
An item may override this default per category via custom_override_withholding /
custom_withholding_action / custom_withholding_override_category — see
upande_accounting.utils.category_base_from_item_rows, the same function that
calculates the actual withholding on the invoice.
Only when no category can be matched (legacy/manually edited rows) does the
report fall back to reversing the base out of tax_amount / rate, and finally to
the invoice's base_tax_withholding_net_total.

Residential Status derived from supplier country (Kenya = Resident, else Non Resident).
"""


import frappe
from frappe import _
from frappe.utils import flt

from upande_accounting.utils import (
    _get_withholding_rate_for_date,
    category_base_from_item_rows,
    WITHHOLDING_OVERRIDE_FIELDS,
)


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
            pi.company,
            pi.supplier,
            pi.supplier_name,
            pi.base_tax_withholding_net_total,
            pi.tax_withholding_category,
            pi.custom_withholding_1,
            pi.custom_withholding_2,
            pi.custom_withholding_3,
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
            pit.rate                                        AS stored_rate,
            wtm.payment_date,
            wtm.prn_number
        FROM `tabPurchase Invoice` pi
        JOIN `tabPurchase Taxes and Charges` pit
            ON  pit.parent       = pi.name
            AND pit.account_head IN ({acc_ph})
            AND pit.tax_amount   > 0
        LEFT JOIN `tabWithholding Tax Management` wtm
            ON  wtm.purchase_invoice    = pi.name
            AND wtm.withholding_account = pit.account_head
        LEFT JOIN `tabSupplier` sup ON sup.name = pi.supplier
        WHERE pi.docstatus = 1
        {conditions}
        ORDER BY pi.posting_date ASC, pi.supplier ASC
    """.format(acc_ph=acc_ph, conditions=conditions)

    rows = frappe.db.sql(sql, tuple(accounts + params), as_dict=True)
    if not rows:
        return []

    return _build_result(rows)


# ---------------------------------------------------------------------------
# Per-invoice category / base resolution
#
# A withholding account is often shared by several Tax Withholding Categories
# (different rates over time, goods vs. services, ...). Resolving the category
# from the account alone is ambiguous — instead only the categories actually
# selected on the invoice (tax_withholding_category + custom_withholding_1/2/3)
# are considered, exactly like upande_accounting.utils does when the
# withholding was first calculated.
# ---------------------------------------------------------------------------

def _build_result(rows):
    company = rows[0].get("company")

    invoice_categories = {}
    all_categories = set()
    for row in rows:
        cats = []
        for f in ("tax_withholding_category", "custom_withholding_1", "custom_withholding_2", "custom_withholding_3"):
            val = row.get(f)
            if val and val not in cats:
                cats.append(val)
        invoice_categories[row["invoice_number"]] = cats
        all_categories.update(cats)

    # {category: {account, ...}} — restricted to the accounts this report already filtered on
    accounts_by_category = {}
    if all_categories:
        twa_rows = frappe.get_all(
            "Tax Withholding Account",
            filters={"parent": ["in", list(all_categories)], "company": company},
            fields=["parent", "account"],
        )
        for r in twa_rows:
            accounts_by_category.setdefault(r.parent, set()).add(r.account)

    category_meta = {}
    if all_categories:
        for r in frappe.get_all(
            "Tax Withholding Category",
            filters={"name": ["in", list(all_categories)]},
            fields=["name", "nature_of_transaction", "custom_applicable_for_services"],
        ):
            category_meta[r.name] = r

    invoice_numbers = list(invoice_categories.keys())
    items_by_invoice = _get_invoice_items(invoice_numbers)

    result = []
    for row in rows:
        invoice_number = row["invoice_number"]
        categories = invoice_categories.get(invoice_number, [])

        matched_category = None
        for cat in categories:
            if row["withholding_account"] in accounts_by_category.get(cat, set()):
                matched_category = cat
                break

        meta = category_meta.get(matched_category)
        nature_of_transaction = (meta.nature_of_transaction if meta else None) or "Other Income"

        rate = None
        if matched_category:
            rate = _get_withholding_rate_for_date(matched_category, row.get("posting_date"))
        if not rate:
            rate = flt(row.get("stored_rate"))

        is_service_only = bool(meta and meta.custom_applicable_for_services)
        base = 0.0
        if matched_category:
            base = category_base_from_item_rows(
                items_by_invoice.get(invoice_number, []),
                matched_category,
                is_service_only,
                amount_field="base_net_amount",
            )

        if not base:
            # No matching category, or the item flags weren't set (legacy data) —
            # fall back to reversing the base out of the tax amount, then to the
            # invoice's own withholding net total as a last resort.
            if rate:
                base = flt(row.get("tax_amount")) * 100 / rate
            else:
                base = flt(row.get("base_tax_withholding_net_total"))

        country = (row.get("country") or "").strip()
        result.append({
            "nature_of_transaction": nature_of_transaction,
            "country":               country or "Kenya",
            "residential_status":    "Resident" if country.lower() == "kenya" else "Non Resident",
            "payment_date":          row.get("payment_date"),
            "tax_id":                row.get("tax_id") or "",
            "supplier_name":         row.get("supplier_name") or row.get("supplier") or "",
            "bill_no":               row.get("bill_no") or invoice_number or "",
            "email":                 row.get("email") or "",
            "gross_amount":          flt(base, 2),
            "tax_rate":              flt(rate, 2),
            "tax_amount":            flt(row.get("tax_amount")),
            "_invoice_number":       invoice_number,
            "_prn_number":           row.get("prn_number"),
        })

    return result


def _get_invoice_items(invoice_numbers):
    """
    Per-invoice item rows, with the fields category_base_from_item_rows needs.
    Matches the calc in upande_accounting.utils.recalculate_withholding_tax_amounts,
    including per-item withholding overrides.

    Uses base_net_amount (company currency) rather than net_amount (document
    currency) because tax_amount/gross_amount in this report are both taken
    from the base-currency tax row fields — mixing currencies here would make
    the gross-amount-vs-rate math come out wrong for foreign-currency suppliers.
    """
    if not invoice_numbers:
        return {}

    rows = frappe.get_all(
        "Purchase Invoice Item",
        filters={"parent": ["in", invoice_numbers]},
        fields=["parent", "base_net_amount", *WITHHOLDING_OVERRIDE_FIELDS],
    )
    items_by_invoice = {}
    for r in rows:
        items_by_invoice.setdefault(r.parent, []).append(r)
    return items_by_invoice


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
        conditions.append("pi.posting_date >= %s")
        params.append(filters["from_date"])

    if filters.get("to_date"):
        conditions.append("pi.posting_date <= %s")
        params.append(filters["to_date"])

    if filters.get("supplier"):
        conditions.append("pi.supplier = %s")
        params.append(filters["supplier"])

    if filters.get("paid_only", 1):
        conditions.append("pi.status = 'Paid'")

    cond_str = ("AND " + " AND ".join(conditions)) if conditions else ""
    return cond_str, params
# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Withholding VAT KRA Report
==========================
KRA-compatible Withholding VAT filing summary.

Only includes Purchase Invoices where the linked Withholding Tax Management
record has payment_status = 'Paid', meaning the withheld VAT has already
been remitted to KRA.

Column order matches KRA upload format:
  PIN | Invoice Number | Invoice Date | Taxable Amount |
  WHT VAT Rate (%) | WHT VAT Amount | Payment Date | PRN Number

Accounts resolved via is_tax_report_account + tax_report_type IN
('Withholding VAT', 'WHVAT') on the Account master.

Taxable Amount is resolved per invoice, the same way as the Withholding Tax
KRA Report: NOT the invoice's overall gross/net total, but the actual
item-level base the rate was applied to —
  - Service-only categories → net_amount summed over items flagged
    custom_is_service_item=1.
  - All-item categories      → net_amount summed over items flagged
    apply_tds=1.
An item may override this default per category via custom_override_withholding /
custom_withholding_action / custom_withholding_override_category — see
upande_accounting.utils.category_base_from_item_rows, the same function that
calculates the actual withholding on the invoice.
The rate is looked up from the Tax Withholding Category (as of the invoice's
posting date) rather than trusted from the tax row's own `rate` field, which
is 0 on older/legacy rows and would otherwise force the taxable amount into
the fallback "entire invoice" figure below. Only when no category can be
matched (legacy/manually edited rows with none of the invoice's category
fields pointing at the account) does the report fall back to reversing the
base out of tax_amount / rate, and finally to base_tax_withholding_net_total.
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
            pi.company,
            pi.supplier,
            pi.supplier_name,
            pi.base_tax_withholding_net_total,
            pi.tax_withholding_category,
            pi.custom_withholding_1,
            pi.custom_withholding_2,
            pi.custom_withholding_3,
            sup.tax_id,
            pit.account_head                                    AS withholding_account,
            pit.base_tax_amount_after_discount_amount           AS tax_amount,
            pit.rate                                            AS stored_rate,
            wtm.payment_date,
            wtm.prn_number,
            wtm.name                                            AS wtm_name
        FROM `tabPurchase Invoice` pi
        JOIN `tabPurchase Taxes and Charges` pit
            ON  pit.parent       = pi.name
            AND pit.account_head IN ({acc_ph})
            AND pit.tax_amount   > 0
        JOIN `tabWithholding Tax Management` wtm
            ON  wtm.purchase_invoice    = pi.name
            AND wtm.withholding_account = pit.account_head
            AND wtm.payment_status      = 'Paid'
        LEFT JOIN `tabSupplier` sup
            ON  sup.name = pi.supplier
        WHERE pi.docstatus = 1
        {conditions}
        ORDER BY pi.bill_date ASC, pi.supplier ASC
    """.format(acc_ph=acc_ph, conditions=conditions)

    rows = frappe.db.sql(sql, tuple(accounts + params), as_dict=True)
    if not rows:
        return []

    return _build_result(rows)


# ---------------------------------------------------------------------------
# Per-invoice category / taxable-base resolution — mirrors
# withholding_tax_kra_report.py. See module docstring for why the rate and
# taxable amount can't be trusted straight off the tax row.
# ---------------------------------------------------------------------------

def _build_result(rows):
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

    accounts_by_category = {}
    category_meta = {}
    if all_categories:
        for r in frappe.get_all(
            "Tax Withholding Account",
            filters={"parent": ["in", list(all_categories)]},
            fields=["parent", "account"],
        ):
            accounts_by_category.setdefault(r.parent, set()).add(r.account)

        for r in frappe.get_all(
            "Tax Withholding Category",
            filters={"name": ["in", list(all_categories)]},
            fields=["name", "custom_applicable_for_services"],
        ):
            category_meta[r.name] = r

    items_by_invoice = _get_invoice_items(list(invoice_categories.keys()))

    result = []
    for row in rows:
        invoice_number = row["invoice_number"]

        matched_category = None
        for cat in invoice_categories.get(invoice_number, []):
            if row["withholding_account"] in accounts_by_category.get(cat, set()):
                matched_category = cat
                break

        rate = None
        if matched_category:
            rate = _get_withholding_rate_for_date(matched_category, row.get("bill_date"))
        if not rate:
            rate = flt(row.get("stored_rate"))

        meta = category_meta.get(matched_category)
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
            if rate:
                base = flt(row.get("tax_amount")) * 100 / rate
            else:
                base = flt(row.get("base_tax_withholding_net_total"))

        result.append({
            "tax_id":         row.get("tax_id") or "",
            "supplier_name":  row.get("supplier_name") or row.get("supplier") or "",
            "bill_no":        row.get("bill_no") or invoice_number or "",
            "bill_date":      row.get("bill_date"),
            "taxable_amount": flt(base, 2),
            "tax_rate":       flt(rate, 2),
            "tax_amount":     flt(row.get("tax_amount")),
            "payment_date":   row.get("payment_date"),
            "invoice_number": invoice_number,
        })

    return result


def _get_invoice_items(invoice_numbers):
    """
    Per-invoice item rows, with the fields category_base_from_item_rows needs, in
    base (KES) currency — matches the currency of tax_amount above
    (pit.base_tax_amount_after_discount_amount).
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
    params = []

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

    cond_str = ("AND " + " AND ".join(conditions)) if conditions else ""
    return cond_str, params

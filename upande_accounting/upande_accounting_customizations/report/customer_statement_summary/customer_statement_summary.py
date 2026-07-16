# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Customer Statement Summary — one row per customer showing their balance
over a selected period, derived from GL entries against receivable accounts.

Columns:
  Customer | Customer Name | Currency |
  Opening Balance | Invoiced (period) | Received (period) |
  Draft Invoices | Closing Balance

Filters:
  company, from_date, to_date,
  show_in_company_currency (Check) — toggle party vs base currency,
  include_draft (Check) — add unsubmitted SI amounts to closing balance.

Clicking a customer row navigates to Customer Statement Of Account
with the same company/date/draft filters pre-filled (handled in JS).
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)
    columns = get_columns(filters)
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


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns(filters):
    show_base = filters.get("show_in_company_currency")
    currency_label = _("Company Currency") if show_base else _("Currency")
    amount_options = "" if show_base else "currency"

    return [
        {
            "label":     _("Customer"),
            "fieldname": "customer",
            "fieldtype": "Link",
            "options":   "Customer",
            "width":     300,
        },
        {
            "label":     currency_label,
            "fieldname": "currency",
            "fieldtype": "Link",
            "options":   "Currency",
            "width":     80,
        },
        {
            "label":     _("Opening Balance"),
            "fieldname": "opening_balance",
            "fieldtype": "Currency",
            "options":   amount_options,
            "width":     150,
        },
        {
            "label":     _("Invoiced Amount"),
            "fieldname": "period_debit",
            "fieldtype": "Currency",
            "options":   amount_options,
            "width":     150,
        },
        {
            "label":     _("Received Amount"),
            "fieldname": "period_credit",
            "fieldtype": "Currency",
            "options":   amount_options,
            "width":     150,
        },
        {
            "label":     _("Closing Balance"),
            "fieldname": "closing_balance",
            "fieldtype": "Currency",
            "options":   amount_options,
            "width":     150,
        },
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_receivable_accounts(company):
    return frappe.get_all(
        "Account",
        filters={"company": company, "account_type": "Receivable", "is_group": 0},
        pluck="name",
    )


def get_company_currency(company):
    return frappe.db.get_value("Company", company, "default_currency") or "KES"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters):
    company       = filters["company"]
    from_date     = filters["from_date"]
    to_date       = filters["to_date"]
    show_base     = filters.get("show_in_company_currency")
    include_draft = filters.get("include_draft")

    accounts = get_receivable_accounts(company)
    if not accounts:
        frappe.msgprint(_("No receivable accounts found for this company."), indicator="orange")
        return []

    company_currency = get_company_currency(company)
    acc_ph = ", ".join(["%s"] * len(accounts))

    # Single-pass GL query: covering everything up to to_date,
    # partitioned into opening (< from_date) and period (between dates).
    # All positional %s — cannot mix named %(key)s with IN-clause %s placeholders.
    sql = """
        SELECT
            gle.party AS customer,
            SUM(IF(gle.posting_date <  %s, gle.debit_in_account_currency,  0)) AS opening_debit,
            SUM(IF(gle.posting_date <  %s, gle.credit_in_account_currency, 0)) AS opening_credit,
            SUM(IF(gle.posting_date BETWEEN %s AND %s, gle.debit_in_account_currency,  0)) AS period_debit,
            SUM(IF(gle.posting_date BETWEEN %s AND %s, gle.credit_in_account_currency, 0)) AS period_credit,
            SUM(IF(gle.posting_date <  %s, gle.debit,  0)) AS base_opening_debit,
            SUM(IF(gle.posting_date <  %s, gle.credit, 0)) AS base_opening_credit,
            SUM(IF(gle.posting_date BETWEEN %s AND %s, gle.debit,  0)) AS base_period_debit,
            SUM(IF(gle.posting_date BETWEEN %s AND %s, gle.credit, 0)) AS base_period_credit
        FROM `tabGL Entry` gle
        WHERE
            gle.party_type   = 'Customer'
            AND gle.account  IN ({acc_ph})
            AND gle.is_cancelled = 0
            AND gle.posting_date <= %s
            AND gle.company  = %s
        GROUP BY gle.party
    """.format(acc_ph=acc_ph)

    params = tuple([
        from_date, from_date,        # opening debit, credit
        from_date, to_date,          # period debit
        from_date, to_date,          # period credit
        from_date, from_date,        # base opening debit, credit
        from_date, to_date,          # base period debit
        from_date, to_date,          # base period credit
    ] + accounts + [to_date, company])

    gl_rows = frappe.db.sql(sql, params, as_dict=True)

    gl_customer_set = {r.customer for r in gl_rows} if gl_rows else set()

    # Fetch customer details (name + currency) — for GL customers
    cust_map = {}
    if gl_rows:
        cust_details = frappe.get_all(
            "Customer",
            filters={"name": ["in", list(gl_customer_set)]},
            fields=["name", "customer_name", "default_currency"],
        )
        cust_map = {r.name: r for r in cust_details}

    # Draft invoice totals for ALL customers in the company/period
    # (not just GL customers — a customer with only drafts should also appear)
    draft_map = {}
    base_draft_map = {}
    if include_draft:
        draft_rows = frappe.db.sql(
            """
            SELECT
                customer,
                SUM(grand_total)      AS draft_total,
                SUM(base_grand_total) AS base_draft_total
            FROM `tabSales Invoice`
            WHERE
                docstatus    = 0
                AND posting_date BETWEEN %s AND %s
                AND company  = %s
            GROUP BY customer
            """,
            (from_date, to_date, company),
            as_dict=True,
        )
        draft_map      = {r.customer: flt(r.draft_total)      for r in draft_rows}
        base_draft_map = {r.customer: flt(r.base_draft_total) for r in draft_rows}

    result = []

    # --- rows backed by GL entries ---
    for row in gl_rows:
        cust = cust_map.get(row.customer, frappe._dict())
        cust_currency = cust.get("default_currency") or company_currency

        if show_base:
            opening  = flt(row.base_opening_debit) - flt(row.base_opening_credit)
            p_debit  = flt(row.base_period_debit) + (base_draft_map.get(row.customer, 0.0) if include_draft else 0.0)
            p_credit = flt(row.base_period_credit)
            currency = company_currency
        else:
            opening  = flt(row.opening_debit) - flt(row.opening_credit)
            p_debit  = flt(row.period_debit) + (draft_map.get(row.customer, 0.0) if include_draft else 0.0)
            p_credit = flt(row.period_credit)
            currency = cust_currency

        closing = opening + p_debit - p_credit

        if not any([opening, p_debit, p_credit, closing]):
            continue

        result.append({
            "customer":        row.customer,
            "customer_name":   cust.get("customer_name") or row.customer,
            "currency":        currency,
            "opening_balance": opening,
            "period_debit":    p_debit,
            "period_credit":   p_credit,
            "closing_balance": closing,
        })

    # --- draft-only customers (no GL entries in the whole history) ---
    if include_draft:
        draft_only_ids = [c for c in draft_map if c not in gl_customer_set]
        if draft_only_ids:
            draft_cust_details = frappe.get_all(
                "Customer",
                filters={"name": ["in", draft_only_ids]},
                fields=["name", "customer_name", "default_currency"],
            )
            draft_cust_map = {r.name: r for r in draft_cust_details}

            for cust_id in draft_only_ids:
                cust = draft_cust_map.get(cust_id, frappe._dict())
                cust_currency = cust.get("default_currency") or company_currency
                draft = base_draft_map.get(cust_id, 0.0) if show_base else draft_map.get(cust_id, 0.0)
                currency = company_currency if show_base else cust_currency

                result.append({
                    "customer":        cust_id,
                    "customer_name":   cust.get("customer_name") or cust_id,
                    "currency":        currency,
                    "opening_balance": 0.0,
                    "period_debit":    draft,
                    "period_credit":   0.0,
                    "closing_balance": draft,
                })

    result.sort(key=lambda r: (r["customer_name"] or "").lower())
    return result

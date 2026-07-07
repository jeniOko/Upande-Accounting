# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Supplier Statement Summary — one row per supplier showing their balance
over a selected period, derived from GL entries against payable accounts.

Columns:
  Supplier | Supplier Name | Currency |
  Opening Balance | Billed (period) | Paid (period) |
  Draft Bills | Closing Balance

Balance sign convention: a Purchase Invoice increases the amount owed
(credit to the payable account), a Payment Entry decreases it (debit to the
payable account). Balances are therefore accumulated as credit - debit, so a
positive balance means the company owes the supplier.

Filters:
  company, from_date, to_date,
  show_in_company_currency (Check) — toggle party vs base currency,
  include_draft (Check) — add unsubmitted PI amounts to closing balance.

Clicking a supplier row navigates to Supplier Statement Of Account
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
            "label":     _("Supplier"),
            "fieldname": "supplier",
            "fieldtype": "Link",
            "options":   "Supplier",
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
            "label":     _("Billed Amount"),
            "fieldname": "period_credit",
            "fieldtype": "Currency",
            "options":   amount_options,
            "width":     150,
        },
        {
            "label":     _("Paid Amount"),
            "fieldname": "period_debit",
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

def get_payable_accounts(company):
    return frappe.get_all(
        "Account",
        filters={"company": company, "account_type": "Payable", "is_group": 0},
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

    accounts = get_payable_accounts(company)
    if not accounts:
        frappe.msgprint(_("No payable accounts found for this company."), indicator="orange")
        return []

    company_currency = get_company_currency(company)
    acc_ph = ", ".join(["%s"] * len(accounts))

    # Single-pass GL query: covering everything up to to_date,
    # partitioned into opening (< from_date) and period (between dates).
    # All positional %s — cannot mix named %(key)s with IN-clause %s placeholders.
    sql = """
        SELECT
            gle.party AS supplier,
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
            gle.party_type   = 'Supplier'
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

    gl_supplier_set = {r.supplier for r in gl_rows} if gl_rows else set()

    # Fetch supplier details (name + currency) — for GL suppliers
    supp_map = {}
    if gl_rows:
        supp_details = frappe.get_all(
            "Supplier",
            filters={"name": ["in", list(gl_supplier_set)]},
            fields=["name", "supplier_name", "default_currency"],
        )
        supp_map = {r.name: r for r in supp_details}

    # Draft bill totals for ALL suppliers in the company/period
    # (not just GL suppliers — a supplier with only drafts should also appear)
    draft_map = {}
    base_draft_map = {}
    if include_draft:
        draft_rows = frappe.db.sql(
            """
            SELECT
                supplier,
                SUM(grand_total)      AS draft_total,
                SUM(base_grand_total) AS base_draft_total
            FROM `tabPurchase Invoice`
            WHERE
                docstatus    = 0
                AND posting_date BETWEEN %s AND %s
                AND company  = %s
            GROUP BY supplier
            """,
            (from_date, to_date, company),
            as_dict=True,
        )
        draft_map      = {r.supplier: flt(r.draft_total)      for r in draft_rows}
        base_draft_map = {r.supplier: flt(r.base_draft_total) for r in draft_rows}

    result = []

    # --- rows backed by GL entries ---
    for row in gl_rows:
        supp = supp_map.get(row.supplier, frappe._dict())
        supp_currency = supp.get("default_currency") or company_currency

        if show_base:
            opening  = flt(row.base_opening_credit) - flt(row.base_opening_debit)
            p_credit = flt(row.base_period_credit) + (base_draft_map.get(row.supplier, 0.0) if include_draft else 0.0)
            p_debit  = flt(row.base_period_debit)
            currency = company_currency
        else:
            opening  = flt(row.opening_credit) - flt(row.opening_debit)
            p_credit = flt(row.period_credit) + (draft_map.get(row.supplier, 0.0) if include_draft else 0.0)
            p_debit  = flt(row.period_debit)
            currency = supp_currency

        closing = opening + p_credit - p_debit

        if not any([opening, p_debit, p_credit, closing]):
            continue

        result.append({
            "supplier":        row.supplier,
            "supplier_name":   supp.get("supplier_name") or row.supplier,
            "currency":        currency,
            "opening_balance": opening,
            "period_credit":   p_credit,
            "period_debit":    p_debit,
            "closing_balance": closing,
        })

    # --- draft-only suppliers (no GL entries in the whole history) ---
    if include_draft:
        draft_only_ids = [s for s in draft_map if s not in gl_supplier_set]
        if draft_only_ids:
            draft_supp_details = frappe.get_all(
                "Supplier",
                filters={"name": ["in", draft_only_ids]},
                fields=["name", "supplier_name", "default_currency"],
            )
            draft_supp_map = {r.name: r for r in draft_supp_details}

            for supp_id in draft_only_ids:
                supp = draft_supp_map.get(supp_id, frappe._dict())
                supp_currency = supp.get("default_currency") or company_currency
                draft = base_draft_map.get(supp_id, 0.0) if show_base else draft_map.get(supp_id, 0.0)
                currency = company_currency if show_base else supp_currency

                result.append({
                    "supplier":        supp_id,
                    "supplier_name":   supp.get("supplier_name") or supp_id,
                    "currency":        currency,
                    "opening_balance": 0.0,
                    "period_credit":   draft,
                    "period_debit":    0.0,
                    "closing_balance": draft,
                })

    result.sort(key=lambda r: (r["supplier_name"] or "").lower())
    return result

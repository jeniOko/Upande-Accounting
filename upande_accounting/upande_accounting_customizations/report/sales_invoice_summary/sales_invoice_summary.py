# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe


"""
Dynamic column report showing Sales Invoice totals grouped by customer.

Layout:
  Fixed columns  : Customer, Currency, Invoice No, Posting Date, Grand Total
  Always-on      : Net Amount
  Dynamic        : one column per tax account head  (account_type = "Tax")
                   one column per charge account head (account_type != "Tax")
                   — columns are auto-detected from data in the selected period
                   — user can further filter via the "Show Columns" multiselect

Row structure per customer (sorted chronologically):
  [invoice row] × N
  [bold subtotal row]   ← inline after last invoice, bold

Grand total row at the very bottom.
"""

import frappe
from frappe import _
from frappe.utils import flt


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)

    # Step 1 — discover all charge account heads used in this period
    all_accounts = get_charge_accounts(filters)      # {name: {label, account_type}}

    # Step 2 — net_only hides all charge/tax columns
    net_only = str(filters.get("net_only", 0)) not in ("0", "False", "false", "")

    # Step 3 — apply user's column filter (ignored when net_only)
    selected_keys = [] if net_only else get_selected_column_keys(filters, all_accounts)

    # Step 4 — build column definitions
    columns = get_columns(selected_keys, all_accounts, net_only)

    # Step 5 — fetch invoice data and pivot charges
    data = get_data(filters, selected_keys, all_accounts)

    # Step 5 — return message if nothing found
    if not data:
        frappe.msgprint(_("No invoices found for the selected filters."), indicator="blue")

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
# Discover charge accounts used in the period
# ---------------------------------------------------------------------------

def get_charge_accounts(filters):
    """
    Return an ordered dict of account heads that appear in
    Sales Taxes and Charges for submitted invoices in the period.

    Structure: { account_head: { "label": str, "account_type": str } }

    Tax accounts (account_type = "Tax") come first, then others.
    """
    conditions = ["stc.parenttype = 'Sales Invoice'", "si.docstatus = 1"]
    values = []

    if filters.get("company"):
        conditions.append("si.company = %s")
        values.append(filters["company"])
    if filters.get("from_date"):
        conditions.append("si.posting_date >= %s")
        values.append(filters["from_date"])
    if filters.get("to_date"):
        conditions.append("si.posting_date <= %s")
        values.append(filters["to_date"])
    if filters.get("customer"):
        conditions.append("si.customer = %s")
        values.append(filters["customer"])

    sql = """
        SELECT
            stc.account_head,
            stc.description         AS charge_label,
            acc.account_type        AS account_type
        FROM `tabSales Taxes and Charges` stc
        JOIN `tabSales Invoice` si  ON si.name = stc.parent
        JOIN `tabAccount` acc       ON acc.name = stc.account_head
        WHERE {cond}
        GROUP BY stc.account_head
        ORDER BY
            CASE WHEN acc.account_type = 'Tax' THEN 0 ELSE 1 END,
            stc.account_head
    """.format(cond=" AND ".join(conditions))

    rows = frappe.db.sql(sql, tuple(values), as_dict=True)

    accounts = {}
    for r in rows:
        # Use account_head as key (safe for column fieldname after sanitising)
        accounts[r.account_head] = {
            "label":        r.charge_label or r.account_head,
            "account_type": r.account_type or "Other",
        }
    return accounts


# ---------------------------------------------------------------------------
# Column key resolution — apply user filter
# ---------------------------------------------------------------------------

def get_selected_column_keys(filters, all_accounts):
    """
    If the user has set 'show_columns', restrict to those keys.
    Otherwise return all discovered account heads.
    show_columns is stored as a newline/comma-separated list of account heads.
    """
    raw = filters.get("show_columns") or ""

    # ERPNext MultiSelectList sends the value as a Python list when the report
    # is executed (e.g. ["VAT - KR", "502016 - ..."]).  It can also arrive as
    # a comma/newline-delimited string in some contexts.  Handle both.
    if isinstance(raw, list):
        selected = [k.strip() for k in raw if k and k.strip()]
    elif raw:
        selected = [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
    else:
        selected = []

    if not selected:
        return list(all_accounts.keys())

    # Preserve discovery order, keep only what the user selected
    return [k for k in all_accounts if k in selected]


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

def _safe_fieldname(account_head):
    """Convert account head string to a safe fieldname."""
    return "col__" + frappe.scrub(account_head)


def get_columns(selected_keys, all_accounts, net_only=False):
    cols = [
        {
            "label":     _("Customer"),
            "fieldname": "customer",
            "fieldtype": "Link",
            "options":   "Customer",
            "width":     250,
        },
        {
            "label":     _("Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width":     120,
        },
     
        {
            "label":     _("Invoice No"),
            "fieldname": "invoice_no",
            "fieldtype": "Link",
            "options":   "Sales Invoice",
            "width":     180,
        },
        
           {
            "label":     _("Currency"),
            "fieldname": "currency",
            "fieldtype": "Link",
            "options":   "Currency",
            "width":     60,
        },
        {
            "label":     _("Net Amount"),
            "fieldname": "net_total",
            "fieldtype": "Currency",
            "options":   "currency",
            "width":     140,
        },
    ]

    # Dynamic columns — tax accounts first (auto-sorted by get_charge_accounts)
    for key in selected_keys:
        info = all_accounts[key]
        label = info["label"]
        if info["account_type"] == "Tax":
            label = "Tax: " + label
        else:
            label = "Charge: " + label

        cols.append({
            "label":     _(label),
            "fieldname": _safe_fieldname(key),
            "fieldtype": "Currency",
            "options":   "currency",
            "width":     140,
        })

    cols.append({
        "label":     _("Grand Total"),
        "fieldname": "grand_total",
        "fieldtype": "Currency",
        "options":   "currency",
        "width":     150,
    })

    # Company-currency totals — always shown, clearly labelled
    # These appear on every row but are most meaningful on the grand total row.
    cols += [
        {
            "label":     _("Net Amount (Co. Currency)"),
            "fieldname": "base_net_total",
            "fieldtype": "Currency",
            "width":     170,
        },
        {
            "label":     _("Grand Total (Co. Currency)"),
            "fieldname": "base_grand_total",
            "fieldtype": "Currency",
            "width":     175,
        },
    ]

    return cols


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters, selected_keys, all_accounts):
    conditions = ["si.docstatus = 1"]
    values = []

    if filters.get("company"):
        conditions.append("si.company = %s")
        values.append(filters["company"])
    if filters.get("from_date"):
        conditions.append("si.posting_date >= %s")
        values.append(filters["from_date"])
    if filters.get("to_date"):
        conditions.append("si.posting_date <= %s")
        values.append(filters["to_date"])
    if filters.get("customer"):
        conditions.append("si.customer = %s")
        values.append(filters["customer"])

    # Base invoice query
    inv_sql = """
        SELECT
            si.name             AS invoice_no,
            si.customer         AS customer,
            si.customer_name    AS customer_name,
            si.posting_date     AS posting_date,
            si.currency         AS currency,
            si.net_total        AS net_total,
            si.grand_total      AS grand_total,
            si.base_net_total   AS base_net_total,
            si.base_grand_total AS base_grand_total,
            si.is_return        AS is_return
        FROM `tabSales Invoice` si
        WHERE {cond}
        ORDER BY
            si.customer ASC,
            si.posting_date ASC,
            si.name ASC
    """.format(cond=" AND ".join(conditions))

    invoices = frappe.db.sql(inv_sql, tuple(values), as_dict=True)
    if not invoices:
        return []

    invoice_names = [inv.invoice_no for inv in invoices]

    # Fetch all charge rows for these invoices in one query
    if invoice_names and selected_keys:
        ph = ", ".join(["%s"] * len(invoice_names))
        charge_sql = """
            SELECT
                stc.parent              AS invoice_no,
                stc.account_head        AS account_head,
                SUM(stc.tax_amount)     AS amount,
                SUM(stc.base_tax_amount) AS base_amount
            FROM `tabSales Taxes and Charges` stc
            WHERE stc.parent IN ({ph})
              AND stc.account_head IN ({acc_ph})
            GROUP BY stc.parent, stc.account_head
        """.format(
            ph=ph,
            acc_ph=", ".join(["%s"] * len(selected_keys)),
        )
        charge_rows = frappe.db.sql(
            charge_sql,
            tuple(invoice_names + selected_keys),
            as_dict=True,
        )
        # Build lookup: { invoice_no: { account_head: amount } }
        charge_map = {}
        for cr in charge_rows:
            charge_map.setdefault(cr.invoice_no, {})[cr.account_head] = {
                "amount":      flt(cr.amount),
                "base_amount": flt(cr.base_amount),
            }
    else:
        charge_map = {}

    # Assemble rows grouped by customer
    data = []

    # Grand total accumulators
    gt = _zero_accumulators(selected_keys)

    # Group invoices by customer (already ordered by customer then date)
    from itertools import groupby
    for customer, inv_iter in groupby(invoices, key=lambda x: x.customer):
        inv_list = list(inv_iter)

        # Customer-level accumulators
        ct = _zero_accumulators(selected_keys)

        for inv in inv_list:
            charges = charge_map.get(inv.invoice_no, {})
            row = {
                "customer":         inv.customer,
                "currency":         inv.currency,
                "invoice_no":       inv.invoice_no,
                "posting_date":     inv.posting_date,
                "net_total":        flt(inv.net_total),
                "grand_total":      flt(inv.grand_total),
                "base_net_total":   flt(inv.base_net_total),
                "base_grand_total": flt(inv.base_grand_total),
                "is_return":        inv.is_return,
            }

            # Pivot charge columns (transaction currency + base currency)
            for key in selected_keys:
                fn      = _safe_fieldname(key)
                fn_base = _safe_fieldname(key) + "_base"
                charge_data = charges.get(key, {})
                row[fn]      = flt(charge_data.get("amount", 0))
                row[fn_base] = flt(charge_data.get("base_amount", 0))
                ct[fn]       = flt(ct.get(fn, 0)) + row[fn]
                gt[fn]       = flt(gt.get(fn, 0)) + row[fn]
                ct[fn_base]  = flt(ct.get(fn_base, 0)) + row[fn_base]
                gt[fn_base]  = flt(gt.get(fn_base, 0)) + row[fn_base]

            ct["net_total"]        += flt(inv.net_total)
            ct["grand_total"]      += flt(inv.grand_total)
            ct["base_net_total"]   += flt(inv.base_net_total)
            ct["base_grand_total"] += flt(inv.base_grand_total)
            gt["net_total"]        += flt(inv.net_total)
            gt["grand_total"]      += flt(inv.grand_total)
            gt["base_net_total"]   += flt(inv.base_net_total)
            gt["base_grand_total"] += flt(inv.base_grand_total)

            data.append(row)

        # Bold subtotal row — "Total for <Customer Name>"
        customer_name = inv_list[0].customer_name or customer
        subtotal = {
            "customer":         _("Total *{0}").format(customer_name),
            "currency":         inv_list[0].currency,
            "invoice_no":       "",
            "posting_date":     None,
            "net_total":        ct["net_total"],
            "grand_total":      ct["grand_total"],
            "base_net_total":   ct["base_net_total"],
            "base_grand_total": ct["base_grand_total"],
            "is_subtotal":      True,
        }
        for key in selected_keys:
            subtotal[_safe_fieldname(key)]            = ct.get(_safe_fieldname(key), 0)
            subtotal[_safe_fieldname(key) + "_base"]  = ct.get(_safe_fieldname(key) + "_base", 0)
        data.append(subtotal)

        # Empty separator row before the next customer block
        separator = {
            "customer": "", "currency": "", "invoice_no": "",
            "posting_date": None, "net_total": None, "grand_total": None,
            "base_net_total": None, "base_grand_total": None,
            "is_separator": True,
        }
        for key in selected_keys:
            separator[_safe_fieldname(key)]           = None
            separator[_safe_fieldname(key) + "_base"] = None
        data.append(separator)

    # Grand total row
    company_currency = frappe.db.get_value("Company", filters.get("company"), "default_currency") or ""
    grand_row = {
        "customer":         _("Grand Total (Company Currency: {0})").format(company_currency),
        "currency":         company_currency,
        "invoice_no":       "",
        "posting_date":     None,
        "net_total":        gt["base_net_total"],    # show base amounts on grand total row
        "grand_total":      gt["base_grand_total"],
        "base_net_total":   gt["base_net_total"],
        "base_grand_total": gt["base_grand_total"],
        "is_grand_total":   True,
    }
    for key in selected_keys:
        # Grand total row shows base (company currency) charge amounts
        grand_row[_safe_fieldname(key)]           = gt.get(_safe_fieldname(key) + "_base", 0)
        grand_row[_safe_fieldname(key) + "_base"] = gt.get(_safe_fieldname(key) + "_base", 0)
    data.append(grand_row)

    return data


def _zero_accumulators(selected_keys):
    acc = {
        "net_total": 0.0, "grand_total": 0.0,
        "base_net_total": 0.0, "base_grand_total": 0.0,
    }
    for key in selected_keys:
        acc[_safe_fieldname(key)]           = 0.0
        acc[_safe_fieldname(key) + "_base"] = 0.0
    return acc


# ---------------------------------------------------------------------------
# API endpoint — called by JS to populate the show_columns multiselect
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_dynamic_columns_for_filter(company=None, from_date=None, to_date=None, customer=None):
    """
    Returns a list of {value, label, group} dicts for the show_columns
    multiselect filter in the JS.
    """
    filters = frappe._dict(
        company=company, from_date=from_date, to_date=to_date, customer=customer
    )
    accounts = get_charge_accounts(filters)
    result = []
    for account_head, info in accounts.items():
        group = "Tax" if info["account_type"] == "Tax" else "Other Charges"
        result.append({
            "value": account_head,
            "label": info["label"],
            "group": group,
        })
    return result
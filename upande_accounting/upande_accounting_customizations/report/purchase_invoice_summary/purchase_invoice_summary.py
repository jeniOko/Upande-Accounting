# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe
"""

Dynamic column report showing Purchase Invoice totals grouped by supplier.

Layout:
  Fixed columns   : Supplier, Currency, Invoice No, Bill No, Posting Date, Net Amount
  Dynamic tax cols: one column per tax account head (account_type = "Tax"),
                    auto-detected from data in the period, filterable via show_columns
  Collapsed col   : Additional Charges — sum of ALL non-tax charge rows per invoice
  Fixed end cols  : Grand Total, Net Amount (Co. Currency), Grand Total (Co. Currency)

Row structure per supplier (sorted chronologically):
  [invoice row] × N
  [bold "Total for <Supplier Name>" row]
  [empty separator row]
  ...
  [Grand Total row — company currency]

net_only checkbox: collapses all tax + charge columns, shows Net + Grand Total only.
"""

import frappe
from frappe import _
from frappe.utils import flt
from itertools import groupby


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)

    net_only = str(filters.get("net_only", 0)) not in ("0", "False", "false", "")

    # Discover tax account heads used in the period
    tax_accounts = get_tax_accounts(filters)

    # Apply user's column filter (ignored when net_only)
    selected_tax_keys = [] if net_only else get_selected_tax_keys(filters, tax_accounts)

    columns = get_columns(selected_tax_keys, tax_accounts, net_only)
    data    = get_data(filters, selected_tax_keys, net_only)

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
# Discover TAX account heads in the period
# ---------------------------------------------------------------------------

def get_tax_accounts(filters):
    """
    Return ordered dict of TAX-type account heads that appear in
    Purchase Taxes and Charges for submitted invoices in the period.

    { account_head: { "label": str } }
    """
    conditions = ["ptc.parenttype = 'Purchase Invoice'", "pi.docstatus = 1"]
    values = []

    if filters.get("company"):
        conditions.append("pi.company = %s")
        values.append(filters["company"])
    if filters.get("from_date"):
        conditions.append("pi.posting_date >= %s")
        values.append(filters["from_date"])
    if filters.get("to_date"):
        conditions.append("pi.posting_date <= %s")
        values.append(filters["to_date"])
    if filters.get("supplier"):
        conditions.append("pi.supplier = %s")
        values.append(filters["supplier"])

    sql = """
        SELECT
            ptc.account_head,
            ptc.description     AS charge_label,
            acc.account_type    AS account_type
        FROM `tabPurchase Taxes and Charges` ptc
        JOIN `tabPurchase Invoice` pi  ON pi.name = ptc.parent
        JOIN `tabAccount` acc          ON acc.name = ptc.account_head
        WHERE {cond}
          AND acc.account_type = 'Tax'
        GROUP BY ptc.account_head
        ORDER BY ptc.account_head
    """.format(cond=" AND ".join(conditions))

    rows = frappe.db.sql(sql, tuple(values), as_dict=True)

    accounts = {}
    for r in rows:
        accounts[r.account_head] = {
            "label": r.charge_label or r.account_head,
        }
    return accounts


# ---------------------------------------------------------------------------
# Column key resolution
# ---------------------------------------------------------------------------

def get_selected_tax_keys(filters, tax_accounts):
    """
    If show_columns is set, restrict to those keys.
    ERPNext MultiSelectList sends a Python list; handle both list and string.
    """
    raw = filters.get("show_columns") or ""

    if isinstance(raw, list):
        selected = [k.strip() for k in raw if k and k.strip()]
    elif raw:
        selected = [k.strip() for k in raw.replace("\n", ",").split(",") if k.strip()]
    else:
        selected = []

    if not selected:
        return list(tax_accounts.keys())

    return [k for k in tax_accounts if k in selected]


# ---------------------------------------------------------------------------
# Safe fieldname
# ---------------------------------------------------------------------------

def _safe_fieldname(account_head):
    return "tax__" + frappe.scrub(account_head)


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

def get_columns(selected_tax_keys, tax_accounts, net_only=False):
    cols = [
        {
            "label":     _("Posting Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width":     120,
        },
        {
            "label":     _("Supplier"),
            "fieldname": "supplier",
            "fieldtype": "Link",
            "options":   "Supplier",
            "width":     250,
        },
        
        {
            "label":     _("Supplier Invoice No"),
            "fieldname": "bill_no",
            "fieldtype": "Data",
            "width":     140,
        },
        
        
        {
            "label":     _("Supplier Date"),
            "fieldname": "bill_date",
            "fieldtype": "Date",
            "width":     120,
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

    if not net_only:
        # One column per discovered tax account head
        for key in selected_tax_keys:
            label = "Tax — " + tax_accounts[key]["label"]
            cols.append({
                "label":     _(label),
                "fieldname": _safe_fieldname(key),
                "fieldtype": "Currency",
                "options":   "currency",
                "width":     150,
            })

        # All non-tax charges collapsed into one column
        cols.append({
            "label":     _("Additional Charges"),
            "fieldname": "additional_charges",
            "fieldtype": "Currency",
            "options":   "currency",
            "width":     150,
        })

    cols += [
        {
            "label":     _("Grand Total"),
            "fieldname": "grand_total",
            "fieldtype": "Currency",
            "options":   "currency",
            "width":     150,
        },
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
        {
            "label":     _("Invoice No"),
            "fieldname": "invoice_no",
            "fieldtype": "Link",
            "options":   "Purchase Invoice",
            "width":     220,
        },
    ]

    return cols


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters, selected_tax_keys, net_only):
    conditions = ["pi.docstatus = 1"]
    values = []

    if filters.get("company"):
        conditions.append("pi.company = %s")
        values.append(filters["company"])
    if filters.get("from_date"):
        conditions.append("pi.posting_date >= %s")
        values.append(filters["from_date"])
    if filters.get("to_date"):
        conditions.append("pi.posting_date <= %s")
        values.append(filters["to_date"])
    if filters.get("supplier"):
        conditions.append("pi.supplier = %s")
        values.append(filters["supplier"])

    inv_sql = """
        SELECT
            pi.name                 AS invoice_no,
            pi.supplier             AS supplier,
            pi.supplier_name        AS supplier_name,
            pi.bill_no              AS bill_no,
            pi.bill_date            AS bill_date,
            pi.posting_date         AS posting_date,
            pi.currency             AS currency,
            pi.net_total            AS net_total,
            pi.grand_total          AS grand_total,
            pi.base_net_total       AS base_net_total,
            pi.base_grand_total     AS base_grand_total,
            pi.is_return            AS is_return
        FROM `tabPurchase Invoice` pi
        WHERE {cond}
        ORDER BY
            pi.supplier ASC,
            pi.posting_date ASC,
            pi.name ASC
    """.format(cond=" AND ".join(conditions))

    invoices = frappe.db.sql(inv_sql, tuple(values), as_dict=True)
    if not invoices:
        return []

    invoice_names = [inv.invoice_no for inv in invoices]
    ph = ", ".join(["%s"] * len(invoice_names))

    # ── Fetch tax rows for selected tax accounts ───────────────────────
    tax_map = {}   # { invoice_no: { account_head: {amount, base_amount} } }
    if not net_only and selected_tax_keys:
        tax_sql = """
            SELECT
                ptc.parent                  AS invoice_no,
                ptc.account_head            AS account_head,
                SUM(ptc.tax_amount)         AS amount,
                SUM(ptc.base_tax_amount)    AS base_amount
            FROM `tabPurchase Taxes and Charges` ptc
            JOIN `tabAccount` acc ON acc.name = ptc.account_head
            WHERE ptc.parent IN ({ph})
              AND ptc.account_head IN ({acc_ph})
              AND acc.account_type = 'Tax'
            GROUP BY ptc.parent, ptc.account_head
        """.format(
            ph=ph,
            acc_ph=", ".join(["%s"] * len(selected_tax_keys)),
        )
        tax_rows = frappe.db.sql(
            tax_sql,
            tuple(invoice_names + selected_tax_keys),
            as_dict=True,
        )
        for r in tax_rows:
            tax_map.setdefault(r.invoice_no, {})[r.account_head] = {
                "amount":      flt(r.amount),
                "base_amount": flt(r.base_amount),
            }

    # ── Fetch and collapse non-tax (additional) charges ───────────────
    additional_map = {}   # { invoice_no: {amount, base_amount} }
    if not net_only:
        addl_sql = """
            SELECT
                ptc.parent                  AS invoice_no,
                SUM(ptc.tax_amount)         AS amount,
                SUM(ptc.base_tax_amount)    AS base_amount
            FROM `tabPurchase Taxes and Charges` ptc
            JOIN `tabAccount` acc ON acc.name = ptc.account_head
            WHERE ptc.parent IN ({ph})
              AND acc.account_type != 'Tax'
            GROUP BY ptc.parent
        """.format(ph=ph)
        addl_rows = frappe.db.sql(
            addl_sql,
            tuple(invoice_names),
            as_dict=True,
        )
        for r in addl_rows:
            additional_map[r.invoice_no] = {
                "amount":      flt(r.amount),
                "base_amount": flt(r.base_amount),
            }

    # ── Assemble rows ─────────────────────────────────────────────────
    data = []
    gt   = _zero_accumulators(selected_tax_keys, net_only)

    for supplier, inv_iter in groupby(invoices, key=lambda x: x.supplier):
        inv_list = list(inv_iter)
        ct = _zero_accumulators(selected_tax_keys, net_only)

        for inv in inv_list:
            row = {
                "supplier":         inv.supplier,
                "currency":         inv.currency,
                "invoice_no":       inv.invoice_no,
                "bill_no":          inv.bill_no or "",
                "bill_date":        inv.bill_date,
                "posting_date":     inv.posting_date,
                "net_total":        flt(inv.net_total),
                "grand_total":      flt(inv.grand_total),
                "base_net_total":   flt(inv.base_net_total),
                "base_grand_total": flt(inv.base_grand_total),
                "is_return":        inv.is_return,
            }

            if not net_only:
                # Tax columns
                inv_taxes = tax_map.get(inv.invoice_no, {})
                for key in selected_tax_keys:
                    fn = _safe_fieldname(key)
                    d  = inv_taxes.get(key, {})
                    row[fn]            = flt(d.get("amount", 0))
                    row[fn + "_base"]  = flt(d.get("base_amount", 0))
                    ct[fn]            += row[fn]
                    ct[fn + "_base"]  += row[fn + "_base"]
                    gt[fn]            += row[fn]
                    gt[fn + "_base"]  += row[fn + "_base"]

                # Additional charges (collapsed)
                addl = additional_map.get(inv.invoice_no, {})
                row["additional_charges"]            = flt(addl.get("amount", 0))
                row["additional_charges_base"]       = flt(addl.get("base_amount", 0))
                ct["additional_charges"]            += row["additional_charges"]
                ct["additional_charges_base"]       += row["additional_charges_base"]
                gt["additional_charges"]            += row["additional_charges"]
                gt["additional_charges_base"]       += row["additional_charges_base"]

            ct["net_total"]        += flt(inv.net_total)
            ct["grand_total"]      += flt(inv.grand_total)
            ct["base_net_total"]   += flt(inv.base_net_total)
            ct["base_grand_total"] += flt(inv.base_grand_total)
            gt["net_total"]        += flt(inv.net_total)
            gt["grand_total"]      += flt(inv.grand_total)
            gt["base_net_total"]   += flt(inv.base_net_total)
            gt["base_grand_total"] += flt(inv.base_grand_total)

            data.append(row)

        # Subtotal row
        supplier_name = inv_list[0].supplier_name or supplier
        subtotal = {
            "supplier":         _("Total for {0}").format(supplier_name),
            "currency":         inv_list[0].currency,
            "invoice_no":       "",
            "bill_no":          "",
            "bill_date":        None,
            "posting_date":     None,
            "net_total":        ct["net_total"],
            "grand_total":      ct["grand_total"],
            "base_net_total":   ct["base_net_total"],
            "base_grand_total": ct["base_grand_total"],
            "is_subtotal":      True,
        }
        if not net_only:
            for key in selected_tax_keys:
                fn = _safe_fieldname(key)
                subtotal[fn]           = ct.get(fn, 0)
                subtotal[fn + "_base"] = ct.get(fn + "_base", 0)
            subtotal["additional_charges"]      = ct.get("additional_charges", 0)
            subtotal["additional_charges_base"] = ct.get("additional_charges_base", 0)
        data.append(subtotal)

        # Separator
        sep = {
            "supplier": "", "currency": "", "invoice_no": "", "bill_no": "",
            "bill_date": None, "posting_date": None, "net_total": None, "grand_total": None,
            "base_net_total": None, "base_grand_total": None,
            "is_separator": True,
        }
        if not net_only:
            for key in selected_tax_keys:
                sep[_safe_fieldname(key)]           = None
                sep[_safe_fieldname(key) + "_base"] = None
            sep["additional_charges"]      = None
            sep["additional_charges_base"] = None
        data.append(sep)

    # Grand total row — company currency
    company_currency = frappe.db.get_value(
        "Company", filters.get("company"), "default_currency"
    ) or ""

    grand_row = {
        "supplier":         _("Grand Total (Company Currency: {0})").format(company_currency),
        "currency":         company_currency,
        "invoice_no":       "",
        "bill_no":          "",
        "bill_date":        None,
        "posting_date":     None,
        "net_total":        gt["base_net_total"],
        "grand_total":      gt["base_grand_total"],
        "base_net_total":   gt["base_net_total"],
        "base_grand_total": gt["base_grand_total"],
        "is_grand_total":   True,
    }
    if not net_only:
        for key in selected_tax_keys:
            fn = _safe_fieldname(key)
            grand_row[fn]           = gt.get(fn + "_base", 0)
            grand_row[fn + "_base"] = gt.get(fn + "_base", 0)
        grand_row["additional_charges"]      = gt.get("additional_charges_base", 0)
        grand_row["additional_charges_base"] = gt.get("additional_charges_base", 0)
    data.append(grand_row)

    return data


# ---------------------------------------------------------------------------
# Accumulators
# ---------------------------------------------------------------------------

def _zero_accumulators(selected_tax_keys, net_only):
    acc = {
        "net_total": 0.0, "grand_total": 0.0,
        "base_net_total": 0.0, "base_grand_total": 0.0,
    }
    if not net_only:
        for key in selected_tax_keys:
            acc[_safe_fieldname(key)]           = 0.0
            acc[_safe_fieldname(key) + "_base"] = 0.0
        acc["additional_charges"]      = 0.0
        acc["additional_charges_base"] = 0.0
    return acc


# ---------------------------------------------------------------------------
# API — populate show_columns multiselect
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_dynamic_columns_for_filter(company=None, from_date=None, to_date=None, supplier=None):
    """
    Returns [{value, label}] of TAX account heads for the show_columns filter.
    Non-tax charges are always collapsed and don't need to be listed here.
    """
    filters = frappe._dict(
        company=company, from_date=from_date, to_date=to_date, supplier=supplier
    )
    accounts = get_tax_accounts(filters)
    return [
        {"value": head, "label": info["label"]}
        for head, info in accounts.items()
    ]
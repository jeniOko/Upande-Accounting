# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Produces a chronological statement of all transactions for a customer
within a date range, with:
  - Opening balance (balance brought forward before from_date)
  - Transaction lines: date, document type, ref, description, debit, credit, running balance
  - Closing balance
  - Ageing buckets (optional via show_ageing filter): Current, 1-30, 31-60, 61-90, 90+

Document type display labels:
  - Sales Invoice (is_return=0)  → "Invoice"
  - Sales Invoice (is_return=1)  → "Credit Note"
  - Payment Entry                → "Receipt"  (date shown = reference_date, fallback posting_date)
  - Journal Entry                → "Journal Entry"
  - Others                       → voucher_type as-is

Source: GL Entry against the customer's receivable account(s).
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


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
    if not filters.get("customer"):
        frappe.throw(_("Please select a Customer."))
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("Please set both From Date and To Date."))
    if getdate(filters["from_date"]) > getdate(filters["to_date"]):
        frappe.throw(_("From Date cannot be after To Date."))


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns():
    return [
        {
            "label": _("Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width": 120,
        },
        {
            "label": _("Document Type"),
            "fieldname": "display_type",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("Document No"),
            "fieldname": "voucher_no",
            "fieldtype": "Dynamic Link",
            "options": "voucher_type",   
            "width": 240,
        },
        # {
        #     "label": _("Description"),
        #     "fieldname": "description",
        #     "fieldtype": "Data",
        #     "width": 250,
        # },
        {
            "label": _("Due Date"),
            "fieldname": "due_date",
            "fieldtype": "Date",
            "width": 120,
        },
        {
            "label": _("Debit"),
            "fieldname": "debit",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 130,
        },
        {
            "label": _("Credit"),
            "fieldname": "credit",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 130,
        },
        {
            "label": _("Running Balance"),
            "fieldname": "balance",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 140,
        },
        {
            "label": _("Currency"),
            "fieldname": "currency",
            "fieldtype": "Link",
            "options": "Currency",
            "width": 80,
        },
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_receivable_accounts(company):
    return frappe.get_all(
        "Account",
        filters={
            "company":      company,
            "account_type": "Receivable",
            "is_group":     0,
        },
        pluck="name",
    )


def get_customer_currency(customer, company):
    cust_currency = frappe.db.get_value("Customer", customer, "default_currency")
    if cust_currency:
        return cust_currency
    return frappe.db.get_value("Company", company, "default_currency")


def resolve_voucher(voucher_type, voucher_no):
    """
    Returns a dict:
        display_type  — human label shown in the Document Type column
        display_date  — date to show (reference_date for receipts, else posting_date)
        description   — narrative text
        due_date      — invoice due date or None
    """
    result = {
        "display_type": voucher_type,
        "display_date": None,
        "description":  "",
        "due_date":     None,
    }

    try:
        if voucher_type == "Sales Invoice":
            row = frappe.db.get_value(
                "Sales Invoice", voucher_no,
                ["is_return", "remarks", "due_date"],
                as_dict=True,
            )
            if row:
                result["display_type"] = _("Credit Note") if row.is_return else _("Invoice")
                result["description"]  = row.remarks or result["display_type"]
                result["due_date"]     = row.due_date

        elif voucher_type == "Payment Entry":
            row = frappe.db.get_value(
                "Payment Entry", voucher_no,
                ["mode_of_payment", "reference_no", "reference_date"],
                as_dict=True,
            )
            result["display_type"] = _("Receipt")
            if row:
                parts = [_("Receipt")]
                if row.mode_of_payment: parts.append(row.mode_of_payment)
                if row.reference_no:    parts.append(_("Ref: {0}").format(row.reference_no))
                result["description"]  = " — ".join(parts)
                # Use reference_date (cheque/transfer date) when available
                result["display_date"] = row.reference_date or None

        elif voucher_type == "Journal Entry":
            remarks = frappe.db.get_value("Journal Entry", voucher_no, "user_remark")
            result["display_type"] = _("Journal Entry")
            result["description"]  = remarks or _("Journal Entry")

        else:
            result["description"] = voucher_type

    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Core data builder
# ---------------------------------------------------------------------------

def get_data(filters):
    company      = filters.get("company")
    customer     = filters["customer"]
    from_date    = filters["from_date"]
    to_date      = filters["to_date"]
    show_ageing  = filters.get("show_ageing", 1)
    currency     = get_customer_currency(customer, company)

    accounts = get_receivable_accounts(company)
    if not accounts:
        frappe.msgprint(_("No receivable accounts found for this company."))
        return []

    acc_placeholders = ", ".join(["%s"] * len(accounts))

    # ------------------------------------------------------------------
    # 1. Opening balance — all GL entries BEFORE from_date
    # ------------------------------------------------------------------
    opening_sql = """
        SELECT
            SUM(gle.debit_in_account_currency)  AS total_debit,
            SUM(gle.credit_in_account_currency) AS total_credit
        FROM `tabGL Entry` gle
        WHERE
            gle.party_type   = 'Customer'
            AND gle.party    = %s
            AND gle.account  IN ({acc})
            AND gle.posting_date < %s
            AND gle.is_cancelled  = 0
            {company_cond}
    """.format(
        acc=acc_placeholders,
        company_cond="AND gle.company = %s" if company else "",
    )

    open_vals = [customer] + accounts + [from_date]
    if company:
        open_vals.append(company)

    opening_row    = frappe.db.sql(opening_sql, tuple(open_vals), as_dict=True)
    opening_debit  = flt(opening_row[0].total_debit)  if opening_row else 0
    opening_credit = flt(opening_row[0].total_credit) if opening_row else 0
    opening_balance = opening_debit - opening_credit

    # ------------------------------------------------------------------
    # 2. Transactions within the period
    # ------------------------------------------------------------------
    txn_sql = """
        SELECT
            gle.posting_date                    AS posting_date,
            gle.voucher_type                    AS voucher_type,
            gle.voucher_no                      AS voucher_no,
            gle.debit_in_account_currency       AS debit,
            gle.credit_in_account_currency      AS credit
        FROM `tabGL Entry` gle
        WHERE
            gle.party_type   = 'Customer'
            AND gle.party    = %s
            AND gle.account  IN ({acc})
            AND gle.posting_date BETWEEN %s AND %s
            AND gle.is_cancelled  = 0
            {company_cond}
        ORDER BY
            gle.posting_date ASC,
            gle.creation ASC
    """.format(
        acc=acc_placeholders,
        company_cond="AND gle.company = %s" if company else "",
    )

    txn_vals = [customer] + accounts + [from_date, to_date]
    if company:
        txn_vals.append(company)

    transactions = frappe.db.sql(txn_sql, tuple(txn_vals), as_dict=True)

    # ------------------------------------------------------------------
    # 3. Assemble rows
    # ------------------------------------------------------------------
    data = []
    running_balance = opening_balance

    # Opening balance row
    data.append({
        "posting_date": from_date,
        "voucher_type": "",
        "display_type": "",
        "voucher_no":   "",
        "description":  _("Opening Balance"),
        "due_date":     None,
        "debit":        opening_debit  if opening_balance >= 0 else 0,
        "credit":       opening_credit if opening_balance <  0 else 0,
        "balance":      opening_balance,
        "currency":     currency,
        "is_opening":   True,
    })

    for txn in transactions:
        running_balance += flt(txn.debit) - flt(txn.credit)
        resolved = resolve_voucher(txn.voucher_type, txn.voucher_no)

        # For receipts use reference_date when available, else fall back to posting_date
        display_date = resolved["display_date"] or txn.posting_date

        data.append({
            "posting_date": display_date,
            "voucher_type": txn.voucher_type,       # kept for Dynamic Link resolution
            "display_type": resolved["display_type"],
            "voucher_no":   txn.voucher_no,
            "description":  resolved["description"],
            "due_date":     resolved["due_date"],
            "debit":        flt(txn.debit),
            "credit":       flt(txn.credit),
            "balance":      running_balance,
            "currency":     currency,
        })

    # Closing balance row
    data.append({
        "posting_date": to_date,
        "voucher_type": "",
        "display_type": "",
        "voucher_no":   "",
        "description":  _("Closing Balance"),
        "due_date":     None,
        "debit":        "",
        "credit":       "",
        "balance":      running_balance,
        "currency":     currency,
        "is_closing":   True,
    })

    # Ageing — appended when show_ageing is 1/True.
    # ERPNext can pass the value as int 1/0 or string "1"/"0" depending on version.
    if str(show_ageing) not in ("0", "False", "false", ""):
        data += get_ageing_summary(transactions, currency)

    return data


# ---------------------------------------------------------------------------
# Ageing summary
# ---------------------------------------------------------------------------

def get_ageing_summary(transactions, currency):
    today = getdate(nowdate())
    invoice_balances  = {}
    invoice_due_dates = {}

    for txn in transactions:
        key = txn.voucher_no
        net = flt(txn.debit) - flt(txn.credit)
        invoice_balances[key] = invoice_balances.get(key, 0) + net
        if txn.voucher_type == "Sales Invoice" and key not in invoice_due_dates:
            row = frappe.db.get_value("Sales Invoice", key, ["due_date", "is_return"], as_dict=True)
            # Exclude credit notes from ageing
            if row and not row.is_return:
                invoice_due_dates[key] = row.due_date

    buckets = {"current": 0, "1_30": 0, "31_60": 0, "61_90": 0, "above_90": 0}

    for voucher_no, balance in invoice_balances.items():
        if balance <= 0:
            continue
        due_date = invoice_due_dates.get(voucher_no)
        if not due_date:
            continue  # skip non-invoice rows (payments, journals) in ageing
        days_overdue = (today - getdate(due_date)).days
        if days_overdue <= 0:
            buckets["current"] += balance
        elif days_overdue <= 30:
            buckets["1_30"] += balance
        elif days_overdue <= 60:
            buckets["31_60"] += balance
        elif days_overdue <= 90:
            buckets["61_90"] += balance
        else:
            buckets["above_90"] += balance

    separator = {
        "posting_date": None, "voucher_type": "", "display_type": "",
        "voucher_no": "", "description": "", "due_date": None,
        "debit": None, "credit": None, "balance": None,
        "currency": currency, "is_separator": True,
    }

    def ageing_row(label, amount):
        return {
            "posting_date": None, "voucher_type": "", "display_type": "",
            "voucher_no": "", "description": label, "due_date": None,
            "debit": None, "credit": None, "balance": amount,
            "currency": currency, "is_ageing": True,
        }

    return [
        separator,
        ageing_row(_("Current (not yet due)"),   buckets["current"]),
        ageing_row(_("1 – 30 days overdue"),     buckets["1_30"]),
        ageing_row(_("31 – 60 days overdue"),    buckets["31_60"]),
        ageing_row(_("61 – 90 days overdue"),    buckets["61_90"]),
        ageing_row(_("Over 90 days overdue"),    buckets["above_90"]),
    ]
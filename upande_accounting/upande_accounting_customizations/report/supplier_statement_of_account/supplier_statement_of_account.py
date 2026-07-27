# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Produces a chronological statement of all transactions for a supplier
within a date range, with:
  - Opening balance (balance brought forward before from_date)
  - Transaction lines: date, document type, ref, description, debit, credit, running balance
  - Closing balance
  - Ageing buckets (optional via show_ageing filter): Current, 1-30, 31-60, 61-90, 90+

Document type display labels:
  - Purchase Invoice (is_return=0)  → "Bill"
  - Purchase Invoice (is_return=1)  → "Debit Note"
  - Payment Entry                   → "Payment"  (date shown = reference_date, fallback posting_date)
  - Journal Entry                   → "Journal Entry"
  - Others                          → voucher_type as-is

Balance sign convention: a Purchase Invoice increases the amount owed
(credit to the payable account), a Payment Entry decreases it (debit to the
payable account). Running balance is therefore accumulated as credit - debit,
so a positive balance means the company owes the supplier.

Source: GL Entry against the supplier's payable account(s).
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
    if not filters.get("supplier"):
        frappe.throw(_("Please select a Supplier."))
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
            "width": 200,
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

def get_payable_accounts(company):
    return frappe.get_all(
        "Account",
        filters={
            "company":      company,
            "account_type": "Payable",
            "is_group":     0,
        },
        pluck="name",
    )


# Exchange Rate revaluation between customer/supplier accounts is booked via a
# Journal Entry whose own voucher_type is "Exchange Gain Or Loss". These entries
# only move the base-currency balance (revaluing the FX position) and never carry
# a party-currency amount, so they add nothing but noise to a party statement —
# excluded everywhere the ledger is queried.
EXCLUDE_EXCHANGE_GAIN_LOSS = """
    AND NOT (
        gle.voucher_type = 'Journal Entry'
        AND EXISTS (
            SELECT 1 FROM `tabJournal Entry` je
            WHERE je.name = gle.voucher_no
              AND je.voucher_type = 'Exchange Gain Or Loss'
        )
    )
"""


def get_supplier_currency(supplier, company):
    supp_currency = frappe.db.get_value("Supplier", supplier, "default_currency")
    if supp_currency:
        return supp_currency
    return frappe.db.get_value("Company", company, "default_currency")


def resolve_voucher(voucher_type, voucher_no):
    """
    Returns a dict:
        display_type  — human label shown in the Document Type column
        display_date  — date to show (reference_date for payments, else posting_date)
        description   — narrative text
        due_date      — bill due date or None
    """
    result = {
        "display_type": voucher_type,
        "display_date": None,
        "description":  "",
        "due_date":     None,
    }

    try:
        if voucher_type == "Draft Purchase Invoice":
            result["display_type"] = _("Draft Bill")
            result["description"]  = _("Draft Bill (unsubmitted)")
            return result

        if voucher_type == "Purchase Invoice":
            row = frappe.db.get_value(
                "Purchase Invoice", voucher_no,
                ["is_return", "remarks", "due_date"],
                as_dict=True,
            )
            if row:
                result["display_type"] = _("Debit Note") if row.is_return else _("Bill")
                result["description"]  = row.remarks or result["display_type"]
                result["due_date"]     = row.due_date

        elif voucher_type == "Payment Entry":
            row = frappe.db.get_value(
                "Payment Entry", voucher_no,
                ["mode_of_payment", "reference_no", "reference_date"],
                as_dict=True,
            )
            result["display_type"] = _("Payment")
            if row:
                parts = [_("Payment")]
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

def get_draft_transactions(supplier, company, from_date, to_date):
    """Return draft Purchase Invoice rows shaped like GL-entry transaction dicts."""
    rows = frappe.db.sql(
        """
        SELECT
            pi.name         AS voucher_no,
            pi.posting_date AS posting_date,
            0               AS debit,
            pi.grand_total  AS credit,
            pi.creation     AS creation,
            'Draft Purchase Invoice' AS voucher_type
        FROM `tabPurchase Invoice` pi
        WHERE
            pi.supplier      = %s
            AND pi.docstatus = 0
            AND pi.posting_date BETWEEN %s AND %s
            AND pi.company   = %s
        ORDER BY pi.posting_date, pi.creation
        """,
        (supplier, from_date, to_date, company),
        as_dict=True,
    )
    return rows


def get_data(filters):
    company       = filters.get("company")
    supplier      = filters["supplier"]
    from_date     = filters["from_date"]
    to_date       = filters["to_date"]
    show_ageing   = filters.get("show_ageing", 1)
    include_draft = filters.get("include_draft")
    currency      = get_supplier_currency(supplier, company)

    # Reference date for aging: to_date, not actual today.
    # This ensures "overdue as of the report date", not "overdue as of right now".
    ref_date       = getdate(to_date)
    aging_interval = get_payment_term_interval(supplier)

    accounts = get_payable_accounts(company)
    if not accounts:
        frappe.msgprint(_("No payable accounts found for this company."))
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
            gle.party_type   = 'Supplier'
            AND gle.party    = %s
            AND gle.account  IN ({acc})
            AND gle.posting_date < %s
            AND gle.is_cancelled  = 0
            {company_cond}
            {exclude_fx}
    """.format(
        acc=acc_placeholders,
        company_cond="AND gle.company = %s" if company else "",
        exclude_fx=EXCLUDE_EXCHANGE_GAIN_LOSS,
    )

    open_vals = [supplier] + accounts + [from_date]
    if company:
        open_vals.append(company)

    opening_row    = frappe.db.sql(opening_sql, tuple(open_vals), as_dict=True)
    opening_debit  = flt(opening_row[0].total_debit)  if opening_row else 0
    opening_credit = flt(opening_row[0].total_credit) if opening_row else 0
    opening_balance = opening_credit - opening_debit

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
            gle.party_type   = 'Supplier'
            AND gle.party    = %s
            AND gle.account  IN ({acc})
            AND gle.posting_date BETWEEN %s AND %s
            AND gle.is_cancelled  = 0
            {company_cond}
            {exclude_fx}
        ORDER BY
            gle.posting_date ASC,
            gle.creation ASC
    """.format(
        acc=acc_placeholders,
        company_cond="AND gle.company = %s" if company else "",
        exclude_fx=EXCLUDE_EXCHANGE_GAIN_LOSS,
    )

    txn_vals = [supplier] + accounts + [from_date, to_date]
    if company:
        txn_vals.append(company)

    transactions = frappe.db.sql(txn_sql, tuple(txn_vals), as_dict=True)

    # Merge draft bills if requested
    if include_draft and company:
        draft_rows = get_draft_transactions(supplier, company, from_date, to_date)
        all_txns = sorted(
            list(transactions) + draft_rows,
            key=lambda r: (str(r.get("posting_date") or ""), str(r.get("creation") or "")),
        )
    else:
        all_txns = list(transactions)

    # ------------------------------------------------------------------
    # 3. Assemble rows
    # ------------------------------------------------------------------
    data = []
    running_balance = opening_balance

    # Opening balance row
    data.append({
        "posting_date": from_date,
        "voucher_type": "",
        "display_type": _("Opening Balance"),
        "voucher_no":   "",
        "description":  _("Opening Balance"),
        "due_date":     None,
        "debit":        opening_debit  if opening_balance <  0 else 0,
        "credit":       opening_credit if opening_balance >= 0 else 0,
        "balance":      opening_balance,
        "currency":     currency,
        "is_opening":   True,
    })

    for txn in all_txns:
        running_balance += flt(txn.credit) - flt(txn.debit)
        resolved = resolve_voucher(txn.voucher_type, txn.voucher_no)

        # For payments use reference_date when available, else fall back to posting_date
        display_date = resolved["display_date"] or txn.posting_date

        is_draft   = (txn.voucher_type == "Draft Purchase Invoice")
        due_date   = resolved["due_date"]

        # Compute aging level per bill row using to_date as reference,
        # so coloring reflects the state on the report date, not today.
        row_ageing_level = None
        if due_date and txn.voucher_type == "Purchase Invoice":
            days_overdue = (ref_date - getdate(due_date)).days
            if days_overdue <= 0:
                row_ageing_level = 0   # current / not yet due
            elif days_overdue <= aging_interval:
                row_ageing_level = 1
            elif days_overdue <= 2 * aging_interval:
                row_ageing_level = 2
            elif days_overdue <= 3 * aging_interval:
                row_ageing_level = 3
            else:
                row_ageing_level = 4

        data.append({
            "posting_date":  display_date,
            "voucher_type":  txn.voucher_type,
            "display_type":  resolved["display_type"],
            "voucher_no":    txn.voucher_no,
            "description":   resolved["description"],
            "due_date":      due_date,
            "debit":         flt(txn.debit),
            "credit":        flt(txn.credit),
            "balance":       running_balance,
            "currency":      currency,
            "is_draft":      is_draft,
            "ageing_level":  row_ageing_level,
        })

    # Closing balance row
    data.append({
        "posting_date": to_date,
        "voucher_type": "",
        "display_type": _("Closing Balance"),
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
        data += get_ageing_summary(
            currency,
            supplier=supplier,
            company=company,
            to_date=to_date,
            accounts=accounts,
        )

    return data


# ---------------------------------------------------------------------------
# Ageing summary
# ---------------------------------------------------------------------------

def get_payment_term_interval(supplier):
    """
    Return the credit_days from the supplier's Payment Terms Template,
    used as the ageing bucket width. Defaults to 30 if unset.
    """
    pt_name = frappe.db.get_value("Supplier", supplier, "payment_terms")
    if pt_name:
        rows = frappe.get_all(
            "Payment Terms Template Detail",
            filters={"parent": pt_name},
            fields=["credit_days"],
            order_by="idx asc",
            limit=1,
        )
        if rows and rows[0].get("credit_days"):
            return int(rows[0].credit_days)
    return 30


def get_ageing_summary(currency, supplier=None, company=None, to_date=None, accounts=None):
    """
    Build 4 ageing buckets sized to the supplier's payment terms interval
    (defaults to 30 days). Buckets are: current, 1×, 2×, 3×, 3×+ the interval.

    Queries ALL GL entries up to to_date (not just the report period) so that
    bills raised before from_date but still outstanding are correctly aged.
    Uses to_date as the reference date so the report reflects the state on that
    day — not the actual current date.
    """
    interval = get_payment_term_interval(supplier) if supplier else 30
    ref_date  = getdate(to_date) if to_date else getdate(nowdate())

    # Outstanding balance per voucher as of to_date
    invoice_balances  = {}
    invoice_due_dates = {}

    if supplier and company and accounts and to_date:
        acc_ph = ", ".join(["%s"] * len(accounts))
        rows = frappe.db.sql(
            """
            SELECT
                voucher_no,
                voucher_type,
                SUM(debit_in_account_currency)  AS total_debit,
                SUM(credit_in_account_currency) AS total_credit
            FROM `tabGL Entry`
            WHERE
                party_type   = 'Supplier'
                AND party    = %s
                AND account  IN ({acc_ph})
                AND posting_date <= %s
                AND is_cancelled  = 0
                AND company  = %s
            GROUP BY voucher_no, voucher_type
            """.format(acc_ph=acc_ph),
            tuple([supplier] + accounts + [to_date, company]),
            as_dict=True,
        )
        for row in rows:
            balance = flt(row.total_credit) - flt(row.total_debit)
            if balance > 0:
                invoice_balances[row.voucher_no] = balance
            if row.voucher_type == "Purchase Invoice" and row.voucher_no not in invoice_due_dates:
                pi = frappe.db.get_value(
                    "Purchase Invoice", row.voucher_no,
                    ["due_date", "is_return"], as_dict=True,
                )
                if pi and not pi.is_return and pi.due_date:
                    invoice_due_dates[row.voucher_no] = pi.due_date

    # 5 slots: 0=current, 1=1×, 2=2×, 3=3×, 4=over 3×
    buckets = [0.0, 0.0, 0.0, 0.0, 0.0]

    for voucher_no, balance in invoice_balances.items():
        due_date = invoice_due_dates.get(voucher_no)
        if not due_date:
            continue
        days_overdue = (ref_date - getdate(due_date)).days
        if days_overdue <= 0:
            buckets[0] += balance
        elif days_overdue <= interval:
            buckets[1] += balance
        elif days_overdue <= 2 * interval:
            buckets[2] += balance
        elif days_overdue <= 3 * interval:
            buckets[3] += balance
        else:
            buckets[4] += balance

    i = interval
    labels = [
        _("Current (not yet due)"),
        _("1 – {0} days overdue").format(i),
        _("{0} – {1} days overdue").format(i + 1, 2 * i),
        _("{0} – {1} days overdue").format(2 * i + 1, 3 * i),
        _("Over {0} days overdue").format(3 * i),
    ]

    separator = {
        "posting_date": None, "voucher_type": "",
        "display_type": _("Ageing Summary"),
        "voucher_no": "", "description": "", "due_date": None,
        "debit": None, "credit": None, "balance": None,
        "currency": currency, "is_separator": True,
    }

    def ageing_row(label, amount, level):
        return {
            "posting_date": None, "voucher_type": "",
            "display_type": label,   # visible in Document Type column
            "voucher_no":   "", "description": label, "due_date": None,
            "debit": None, "credit": None, "balance": amount,
            "currency": currency, "is_ageing": True, "ageing_level": level,
        }

    return [
        separator,
        ageing_row(labels[0], buckets[0], 0),
        ageing_row(labels[1], buckets[1], 1),
        ageing_row(labels[2], buckets[2], 2),
        ageing_row(labels[3], buckets[3], 3),
        ageing_row(labels[4], buckets[4], 4),
    ]

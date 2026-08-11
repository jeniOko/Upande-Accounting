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

import os
from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate
from frappe.utils.pdf import get_pdf


# Exchange Gain Or Loss Journal Entries are system-generated forex revaluation/
# rounding postings, not real customer transactions — exclude them everywhere
# GL Entry is queried in this report (opening balance, transactions, ageing).
EXCLUDE_FOREX_JE = """
    AND NOT EXISTS (
        SELECT 1 FROM `tabJournal Entry` je
        WHERE je.name = gle.voucher_no
          AND gle.voucher_type = 'Journal Entry'
          AND je.voucher_type = 'Exchange Gain Or Loss'
    )
"""


def execute(filters=None):
    filters = filters or {}
    validate_filters(filters)
    columns = get_columns()
    data    = get_data(filters)
    return columns, data


# ---------------------------------------------------------------------------
# Print / PDF
# ---------------------------------------------------------------------------
#
# The report's built-in "Print"/"PDF" buttons (in the Query Report view)
# render html_format client-side with a tiny mustache-style templating engine
# that does not understand real Jinja (no `{% elif %}`, `namespace()`,
# filters, or `frappe.get_doc`). To keep those buttons on the system-default
# tabular print (with column picking + letterhead, like any other report),
# the fancy layout below is named *_print_template.html — not
# customer_statement_of_account.html — so Frappe does not auto-load it as
# html_format. It is rendered here instead, server-side via
# frappe.render_template, and shipped to the browser as a ready-made PDF
# through the "Print Statement" button.

@frappe.whitelist()
def download_statement_pdf(customer, from_date, to_date, company=None, show_ageing=1, include_draft=0, currency=None):
    filters = frappe._dict({
        "customer":      customer,
        "from_date":     from_date,
        "to_date":       to_date,
        "company":       company,
        "show_ageing":   show_ageing,
        "include_draft": include_draft,
    })
    validate_filters(filters)
    data = get_data(filters)

    statement_currency = currency or (data[0]["currency"] if data else get_customer_currency(customer, company))

    html_path = os.path.join(os.path.dirname(__file__), "customer_statement_of_account_print_template.html")
    with open(html_path) as f:
        template = f.read()

    doc_context = frappe._dict({
        "company":   company,
        "customer":  customer,
        "from_date": from_date,
        "to_date":   to_date,
        "currency":  statement_currency,
    })

    html = frappe.render_template(template, {"doc": doc_context, "data": data})

    frappe.local.response.filename     = f"Statement-{customer}-{to_date}.pdf"
    frappe.local.response.filecontent  = get_pdf(html)
    frappe.local.response.type         = "download"


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
        if voucher_type == "Draft Sales Invoice":
            result["display_type"] = _("Draft Invoice")
            result["description"]  = _("Draft Invoice (unsubmitted)")
            return result

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

def get_draft_transactions(customer, company, from_date, to_date):
    """Return draft Sales Invoice rows shaped like GL-entry transaction dicts."""
    rows = frappe.db.sql(
        """
        SELECT
            si.name         AS voucher_no,
            si.posting_date AS posting_date,
            si.grand_total  AS debit,
            0               AS credit,
            si.creation     AS creation,
            'Draft Sales Invoice' AS voucher_type
        FROM `tabSales Invoice` si
        WHERE
            si.customer      = %s
            AND si.docstatus = 0
            AND si.posting_date BETWEEN %s AND %s
            AND si.company   = %s
        ORDER BY si.posting_date, si.creation
        """,
        (customer, from_date, to_date, company),
        as_dict=True,
    )
    return rows


def get_data(filters):
    company       = filters.get("company")
    customer      = filters["customer"]
    from_date     = filters["from_date"]
    to_date       = filters["to_date"]
    show_ageing   = filters.get("show_ageing", 1)
    include_draft = filters.get("include_draft")
    currency      = get_customer_currency(customer, company)

    # Reference date for aging: to_date, not actual today.
    # This ensures "overdue as of the report date", not "overdue as of right now".
    ref_date       = getdate(to_date)
    aging_interval = get_payment_term_interval(customer)

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
            {exclude_forex_je}
            {company_cond}
    """.format(
        acc=acc_placeholders,
        exclude_forex_je=EXCLUDE_FOREX_JE,
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
            {exclude_forex_je}
            {company_cond}
        ORDER BY
            gle.posting_date ASC,
            gle.creation ASC
    """.format(
        acc=acc_placeholders,
        exclude_forex_je=EXCLUDE_FOREX_JE,
        company_cond="AND gle.company = %s" if company else "",
    )

    txn_vals = [customer] + accounts + [from_date, to_date]
    if company:
        txn_vals.append(company)

    transactions = frappe.db.sql(txn_sql, tuple(txn_vals), as_dict=True)

    # Merge draft invoices if requested
    if include_draft and company:
        draft_rows = get_draft_transactions(customer, company, from_date, to_date)
        all_txns = sorted(
            list(transactions) + draft_rows,
            key=lambda r: (str(r.get("posting_date") or ""), str(r.get("creation") or "")),
        )
    else:
        all_txns = list(transactions)

    # Collapse GL-entry rows belonging to the same voucher into a single
    # statement line. A Payment Entry reconciled against several invoices
    # creates one GL Entry per allocation against the receivable account —
    # without this, a single receipt would show up as one row per invoice
    # it was allocated to instead of the total amount received.
    grouped_txns = OrderedDict()
    for txn in all_txns:
        key = (txn.get("voucher_type"), txn.get("voucher_no"))
        group = grouped_txns.get(key)
        if group is None:
            group = frappe._dict({
                "posting_date": txn.get("posting_date"),
                "voucher_type": txn.get("voucher_type"),
                "voucher_no":   txn.get("voucher_no"),
                "debit":        0,
                "credit":       0,
            })
            grouped_txns[key] = group
        group.debit  += flt(txn.get("debit"))
        group.credit += flt(txn.get("credit"))

    all_txns = list(grouped_txns.values())

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
        "debit":        opening_debit  if opening_balance >= 0 else 0,
        "credit":       opening_credit if opening_balance <  0 else 0,
        "balance":      opening_balance,
        "currency":     currency,
        "is_opening":   True,
    })

    for txn in all_txns:
        running_balance += flt(txn.debit) - flt(txn.credit)
        resolved = resolve_voucher(txn.voucher_type, txn.voucher_no)

        # For receipts use reference_date when available, else fall back to posting_date
        display_date = resolved["display_date"] or txn.posting_date

        is_draft   = (txn.voucher_type == "Draft Sales Invoice")
        due_date   = resolved["due_date"]

        # Compute aging level per invoice row using to_date as reference,
        # so coloring reflects the state on the report date, not today.
        row_ageing_level = None
        if due_date and txn.voucher_type == "Sales Invoice":
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
            customer=customer,
            company=company,
            to_date=to_date,
            accounts=accounts,
        )

    return data


# ---------------------------------------------------------------------------
# Ageing summary
# ---------------------------------------------------------------------------

def get_payment_term_interval(customer):
    """
    Return the credit_days from the customer's Payment Terms Template,
    used as the ageing bucket width. Defaults to 30 if unset.
    """
    pt_name = frappe.db.get_value("Customer", customer, "payment_terms")
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


def get_ageing_summary(currency, customer=None, company=None, to_date=None, accounts=None):
    """
    Build 4 ageing buckets sized to the customer's payment terms interval
    (defaults to 30 days). Buckets are: current, 1×, 2×, 3×, 3×+ the interval.

    Queries ALL GL entries up to to_date (not just the report period) so that
    invoices raised before from_date but still outstanding are correctly aged.
    Uses to_date as the reference date so the report reflects the state on that
    day — not the actual current date.
    """
    interval = get_payment_term_interval(customer) if customer else 30
    ref_date  = getdate(to_date) if to_date else getdate(nowdate())

    # Outstanding balance per voucher as of to_date
    invoice_balances  = {}
    invoice_due_dates = {}

    if customer and company and accounts and to_date:
        acc_ph = ", ".join(["%s"] * len(accounts))
        rows = frappe.db.sql(
            """
            SELECT
                voucher_no,
                voucher_type,
                SUM(debit_in_account_currency)  AS total_debit,
                SUM(credit_in_account_currency) AS total_credit
            FROM `tabGL Entry` gle
            WHERE
                party_type   = 'Customer'
                AND party    = %s
                AND account  IN ({acc_ph})
                AND posting_date <= %s
                AND is_cancelled  = 0
                AND company  = %s
                {exclude_forex_je}
            GROUP BY voucher_no, voucher_type
            """.format(acc_ph=acc_ph, exclude_forex_je=EXCLUDE_FOREX_JE),
            tuple([customer] + accounts + [to_date, company]),
            as_dict=True,
        )
        for row in rows:
            balance = flt(row.total_debit) - flt(row.total_credit)
            if balance > 0:
                invoice_balances[row.voucher_no] = balance
            if row.voucher_type == "Sales Invoice" and row.voucher_no not in invoice_due_dates:
                si = frappe.db.get_value(
                    "Sales Invoice", row.voucher_no,
                    ["due_date", "is_return"], as_dict=True,
                )
                if si and not si.is_return and si.due_date:
                    invoice_due_dates[row.voucher_no] = si.due_date

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
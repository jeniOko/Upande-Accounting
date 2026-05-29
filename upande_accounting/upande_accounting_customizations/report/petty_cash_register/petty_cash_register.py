# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe

"""

A cashbook-style report grouped by petty cash account showing:
  - Opening balance (before from_date)
  - Top-ups: Payment Entries (Internal Transfer) crediting the petty cash account
             Journal Entries with a debit line on the petty cash account
  - Outflows: Expense Claims paid via any mode of payment
              (mode of payment shown as a column)
  - Running balance per row
  - Closing balance at end of each account block

Draft and submitted transactions are shown (cancelled are excluded).
Document status is shown as a column with a link to the source document.
Expense claims show whether they have attachments.

Petty cash accounts are auto-detected from Mode of Payment Account records
where the mode of payment name contains "Petty Cash" (case-insensitive),
then further filtered by the selected company.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns():
    return [
        {
            "label":     _("Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width":     120,
        },
                {
            "label":     _("Employee"),
            "fieldname": "employee",
            "fieldtype": "Link",
            "options":   "Employee",
            "width":     140,
        },
        {
            "label":     _("Description"),
            "fieldname": "description",
            "fieldtype": "Data",
            "width":     240,
        },

        {
            "label":     _("Top-up(Debit)"),
            "fieldname": "topup",
            "fieldtype": "Currency",
            "width":     140,
        },
        {
            "label":     _("Expense(Credit)"),
            "fieldname": "expense",
            "fieldtype": "Currency",
            "width":     140,
        },
        {
            "label":     _("Balance"),
            "fieldname": "balance",
            "fieldtype": "Currency",
            "width":     140,
        },
        {
            "label":     _("Has Attachment"),
            "fieldname": "has_attachment",
            "fieldtype": "Data",
            "width":     110,
        },
        {
            "label":     _("Document Type"),
            "fieldname": "doc_type",
            "fieldtype": "Data",
            "width":     140,
        },
        {
            "label":     _("Status"),
            "fieldname": "doc_status",
            "fieldtype": "Data",
            "width":     130,
        },
        
        {
            "label":     _("Document No"),
            "fieldname": "doc_no",
            "fieldtype": "Dynamic Link",
            "options":   "doc_type",
            "width":     180,
        },
        
        
        {
            "label":     _("Mode of Payment"),
            "fieldname": "mode_of_payment",
            "fieldtype": "Data",
            "width":     130,
        },
        
        
    ]


# ---------------------------------------------------------------------------
# Petty cash account detection
# ---------------------------------------------------------------------------

def get_petty_cash_accounts(company):
    """
    Returns a list of account names linked to any Mode of Payment
    whose name contains 'petty cash' (case-insensitive) for this company.
    """
    rows = frappe.db.sql("""
        SELECT DISTINCT mpa.default_account AS account
        FROM   `tabMode of Payment Account` mpa
        JOIN   `tabMode of Payment` mp ON mp.name = mpa.parent
        WHERE  LOWER(mp.name) LIKE %s
          AND  mpa.company = %s
          AND  mpa.default_account IS NOT NULL
    """, ("%petty cash%", company), as_dict=True)

    accounts = [r.account for r in rows if r.account]

    # If a specific account filter is set, narrow down
    return accounts


# ---------------------------------------------------------------------------
# Attachment lookup
# ---------------------------------------------------------------------------

def get_attachment_map(doctype, docnames):
    """
    Returns { docname: count } for how many files are attached.
    """
    if not docnames:
        return {}
    ph = ", ".join(["%s"] * len(docnames))
    rows = frappe.db.sql("""
        SELECT attached_to_name, COUNT(*) AS cnt
        FROM   `tabFile`
        WHERE  attached_to_doctype = %s
          AND  attached_to_name IN ({ph})
        GROUP BY attached_to_name
    """.format(ph=ph), tuple([doctype] + list(docnames)), as_dict=True)
    return {r.attached_to_name: r.cnt for r in rows}


# ---------------------------------------------------------------------------
# Opening balance
# ---------------------------------------------------------------------------

def get_opening_balance(account, company, before_date):
    row = frappe.db.sql("""
        SELECT
            SUM(gle.debit)  AS total_debit,
            SUM(gle.credit) AS total_credit
        FROM `tabGL Entry` gle
        WHERE
            gle.account      = %s
            AND gle.company  = %s
            AND gle.posting_date < %s
            AND gle.is_cancelled = 0
    """, (account, company, before_date), as_dict=True)

    if row:
        return flt(row[0].total_debit) - flt(row[0].total_credit)
    return 0.0


# ---------------------------------------------------------------------------
# Fetch top-ups
# ---------------------------------------------------------------------------

def get_topups(account, company, from_date, to_date):
    """
    Returns list of top-up dicts from:
      1. Payment Entries of type Internal Transfer where paid_to_account = account
      2. Journal Entries with a debit line on the account
    Both draft (docstatus=0) and submitted (docstatus=1) are included.
    """
    topups = []

    # ── Payment Entry Internal Transfers ─────────────────────────────
    pe_rows = frappe.db.sql("""
        SELECT
            pe.name,
            pe.posting_date,
            pe.paid_amount      AS amount,
            pe.remarks,
            pe.docstatus,
            pe.reference_no
        FROM `tabPayment Entry` pe
        WHERE
            pe.payment_type    = 'Internal Transfer'
            AND pe.paid_to     = %s
            AND pe.company     = %s
            AND pe.posting_date BETWEEN %s AND %s
            AND pe.docstatus   IN (0, 1)
        ORDER BY pe.posting_date ASC, pe.creation ASC
    """, (account, company, from_date, to_date), as_dict=True)

    for r in pe_rows:
        topups.append({
            "posting_date":   r.posting_date,
            "doc_type":       "Payment Entry",
            "doc_no":         r.name,
            "doc_status":     "Draft" if r.docstatus == 0 else "Submitted",
            "employee":       "",
            "description":    r.remarks or "Petty Cash Top-up",
            "mode_of_payment": "Internal Transfer",
            "has_attachment": "",
            "topup":          flt(r.paid_amount),
            "expense":        0,
        })

    # ── Journal Entry debit lines ─────────────────────────────────────
    je_rows = frappe.db.sql("""
        SELECT
            je.name,
            je.posting_date,
            jea.debit_in_account_currency   AS amount,
            je.user_remark,
            je.docstatus
        FROM `tabJournal Entry` je
        JOIN `tabJournal Entry Account` jea
            ON  jea.parent  = je.name
            AND jea.account = %s
            AND jea.debit_in_account_currency > 0
        WHERE
            je.company      = %s
            AND je.posting_date BETWEEN %s AND %s
            AND je.docstatus IN (0, 1)
        ORDER BY je.posting_date ASC, je.creation ASC
    """, (account, company, from_date, to_date), as_dict=True)

    for r in je_rows:
        topups.append({
            "posting_date":    r.posting_date,
            "doc_type":        "Journal Entry",
            "doc_no":          r.name,
            "doc_status":      "Draft" if r.docstatus == 0 else "Submitted",
            "employee":        "",
            "description":     r.user_remark or "Petty Cash Top-up (Journal Entry)",
            "mode_of_payment": "Journal Entry",
            "has_attachment":  "",
            "topup":           flt(r.amount),
            "expense":         0,
        })

    return topups


# ---------------------------------------------------------------------------
# Fetch expense claims
# ---------------------------------------------------------------------------

def get_expense_claims(account, company, from_date, to_date):
    """
    Returns expense claims where the payment was made from this petty cash
    account OR any mode of payment linked to this account, plus ALL other
    expense claims paid by any mode of payment in the period.

    We include ALL paid expense claims so the user can see bank-paid ones too.
    Draft (docstatus=0) and submitted (docstatus=1) are included.
    """

    # Find modes of payment linked to this account
    mop_rows = frappe.db.sql("""
        SELECT DISTINCT mpa.parent AS mode_of_payment
        FROM `tabMode of Payment Account` mpa
        WHERE mpa.default_account = %s
          AND mpa.company = %s
    """, (account, company), as_dict=True)
    linked_mops = [r.mode_of_payment for r in mop_rows]

    # Fetch expense claims for this company in range
    # We pull ALL claims and tag which ones match the petty cash account
    rows = frappe.db.sql("""
        SELECT
            ec.name,
            ec.posting_date,
            ec.employee,
            ec.employee_name,
            ec.mode_of_payment,
            ec.total_claimed_amount     AS amount,
            ec.remark,
            ec.docstatus,
            ec.approval_status,
            ec.status,
            ec.payable_account
        FROM `tabExpense Claim` ec
        WHERE
            ec.company      = %s
            AND ec.posting_date BETWEEN %s AND %s
            AND ec.docstatus IN (0, 1)
        ORDER BY ec.posting_date ASC, ec.creation ASC
    """, (company, from_date, to_date), as_dict=True)

    # Include claims where:
    #   a) mode_of_payment matches one linked to this petty cash account, OR
    #   b) mode_of_payment is blank/None (draft claims often have no MoP yet)
    result = []
    for r in rows:
        mop = (r.mode_of_payment or "").strip()
        if mop == "" or (linked_mops and mop in linked_mops) or not linked_mops:
            result.append(r)

    return result, linked_mops



# ---------------------------------------------------------------------------
# Expense claim status resolver
# ---------------------------------------------------------------------------

def _resolve_expense_status(ec):
    """
    Maps ERPNext Expense Claim docstatus + approval_status + status
    to a human-readable label for the report.

      docstatus=0                          → Draft
      docstatus=1 + approval_status=Pending  → Pending Approval
      docstatus=1 + approval_status=Approved + status != Paid → Approved
      docstatus=1 + approval_status=Approved + status == Paid → Paid
      docstatus=1 + approval_status=Rejected → Rejected
    """
    if ec.docstatus == 0:
        return "Draft"
    approval = (ec.approval_status or "").strip()
    status   = (ec.status or "").strip()
    if approval == "Pending":
        return "Pending Approval"
    elif approval == "Rejected":
        return "Rejected"
    elif approval == "Approved":
        return "Paid" if status == "Paid" else "Approved"
    # Fallback
    return "Submitted"


# ---------------------------------------------------------------------------
# Main data builder
# ---------------------------------------------------------------------------

def get_data(filters):
    company   = filters["company"]
    from_date = filters["from_date"]
    to_date   = filters["to_date"]

    petty_cash_accounts = get_petty_cash_accounts(company)

    # Allow filtering to a single account
    if filters.get("petty_cash_account"):
        acct = filters["petty_cash_account"]
        if acct in petty_cash_accounts:
            petty_cash_accounts = [acct]
        else:
            petty_cash_accounts = [acct]  # still show it even if not in detected list

    if not petty_cash_accounts:
        frappe.msgprint(
            _(
                "No petty cash accounts found. Please ensure a Mode of Payment "
                "named 'Petty Cash' exists with a default account set for this company."
            ),
            indicator="orange",
        )
        return []

    # Build attachment map for all expense claims in period
    all_ec_names = frappe.db.sql("""
        SELECT name FROM `tabExpense Claim`
        WHERE company = %s AND posting_date BETWEEN %s AND %s AND docstatus IN (0,1)
    """, (company, from_date, to_date), as_dict=True)
    attachment_map = get_attachment_map(
        "Expense Claim", [r.name for r in all_ec_names]
    )

    data = []

    for account in petty_cash_accounts:

        # ── Account header row ────────────────────────────────────────
        data.append({
            "posting_date":    None,
            "doc_type":        "",
            "doc_no":          "",
            "doc_status":      "",
            "employee":        "",
            "description":     account,
            "mode_of_payment": "",
            "has_attachment":  "",
            "topup":           None,
            "expense":         None,
            "balance":         None,
            "is_account_header": True,
        })

        # ── Opening balance ───────────────────────────────────────────
        opening = get_opening_balance(account, company, from_date)
        data.append({
            "posting_date":    from_date,
            "doc_type":        "",
            "doc_no":          "",
            "doc_status":      "",
            "employee":        "",
            "description":     _("Opening Balance"),
            "mode_of_payment": "",
            "has_attachment":  "",
            "topup":           None,
            "expense":         None,
            "balance":         opening,
            "is_opening":      True,
        })

        running_balance = opening

        # ── Collect and merge all transactions ────────────────────────
        topups  = get_topups(account, company, from_date, to_date)
        show_missing_only = filters.get("show_attachments_only")
        claims, linked_mops = get_expense_claims(account, company, from_date, to_date)

        # Merge into one chronological list
        transactions = []

        for t in topups:
            transactions.append({**t, "_sort_date": t["posting_date"]})

        for ec in claims:
            att_count = attachment_map.get(ec.name, 0)
            # Filter: show only claims without attachments if requested
            if show_missing_only and att_count > 0:
                continue
            transactions.append({
                "posting_date":    ec.posting_date,
                "doc_type":        "Expense Claim",
                "doc_no":          ec.name,
                "doc_status":      _resolve_expense_status(ec),
                "employee":        ec.employee,
                "description":     ec.remark or ec.employee_name or "",
                "mode_of_payment": ec.mode_of_payment or "",
                "has_attachment":  "Yes ({0})".format(att_count) if att_count else "No",
                "topup":           0,
                "expense":         flt(ec.amount) if flt(ec.amount) else flt(ec.get("total_claimed_amount", 0)),
                "_sort_date":      ec.posting_date,
            })

        # Sort by date then by type (top-ups before expenses on same day)
        transactions.sort(key=lambda x: (
            str(x["_sort_date"]) if x["_sort_date"] else "",
            0 if x["topup"] else 1,
        ))

        # ── Append rows with running balance ─────────────────────────
        for txn in transactions:
            running_balance += flt(txn["topup"]) - flt(txn["expense"])
            row = {k: v for k, v in txn.items() if not k.startswith("_")}
            row["balance"] = running_balance
            data.append(row)

        # ── Closing balance ───────────────────────────────────────────
        data.append({
            "posting_date":    to_date,
            "doc_type":        "",
            "doc_no":          "",
            "doc_status":      "",
            "employee":        "",
            "description":     _("Closing Balance"),
            "mode_of_payment": "",
            "has_attachment":  "",
            "topup":           None,
            "expense":         None,
            "balance":         running_balance,
            "is_closing":      True,
        })

        # ── Empty separator before next account ───────────────────────
        data.append({
            "posting_date":    None,
            "doc_type":        "",
            "doc_no":          "",
            "doc_status":      "",
            "employee":        "",
            "description":     "",
            "mode_of_payment": "",
            "has_attachment":  "",
            "topup":           None,
            "expense":         None,
            "balance":         None,
            "is_separator":    True,
        })

    return data

# ---------------------------------------------------------------------------
# API — populate petty cash account filter
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_petty_cash_accounts_for_filter(company=None):
    """
    Returns accounts relevant to petty cash / expense settlement:
      1. Accounts linked to any Mode of Payment named 'Petty Cash'
         for this company.
      2. Accounts that have been used to settle expense claims
         (via Payment Entry linked to an Expense Claim) for this company —
         covers bank accounts used for reimbursements.
    """
    if not company:
        return []

    account_set = set()

    # Source 1 — Mode of Payment named "Petty Cash"
    rows1 = frappe.db.sql("""
        SELECT DISTINCT mpa.default_account AS account
        FROM   `tabMode of Payment Account` mpa
        JOIN   `tabMode of Payment` mp ON mp.name = mpa.parent
        WHERE  LOWER(mp.name) LIKE %s
          AND  mpa.company = %s
          AND  mpa.default_account IS NOT NULL
    """, ("%petty cash%", company), as_dict=True)
    for r in rows1:
        if r.account:
            account_set.add(r.account)

    # Source 2 — accounts used in Payment Entries that settled Expense Claims
    # Payment Entry references an Expense Claim via the reference table
    rows2 = frappe.db.sql("""
        SELECT DISTINCT pe.paid_from AS account
        FROM   `tabPayment Entry` pe
        JOIN   `tabPayment Entry Reference` per
               ON  per.parent            = pe.name
               AND per.reference_doctype = 'Expense Claim'
        WHERE  pe.company    = %s
          AND  pe.docstatus  IN (0, 1)
          AND  pe.paid_from  IS NOT NULL
    """, (company,), as_dict=True)
    for r in rows2:
        if r.account:
            account_set.add(r.account)

    # Source 3 — payable_account on submitted/draft expense claims
    # (catches claims paid directly without a Payment Entry reference)
    rows3 = frappe.db.sql("""
        SELECT DISTINCT ec.payable_account AS account
        FROM   `tabExpense Claim` ec
        WHERE  ec.company      = %s
          AND  ec.docstatus    IN (0, 1)
          AND  ec.payable_account IS NOT NULL
    """, (company,), as_dict=True)
    for r in rows3:
        if r.account:
            account_set.add(r.account)

    return sorted(list(account_set))
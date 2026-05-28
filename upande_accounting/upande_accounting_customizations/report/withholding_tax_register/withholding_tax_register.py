# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe

"""

Tracks all withholding tax obligations (WHTAX + WHVAT) on purchase invoices,
their payment status, PRN numbers, and linked journal entries.

Account detection uses the is_tax_report_account + tax_report_type fields
on the Account doctype (tagged as WHTAX or WHVAT) instead of LIKE patterns.

Nature of Transaction is pulled from the Tax Withholding Category linked
to the supplier via the purchase invoice.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate
from collections import defaultdict


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data    = get_data(filters)
    return columns, data


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns():
    return [
        {
            "label":     _("Select"),
            "fieldname": "select_row",
            "fieldtype": "Check",
            "width":     60,
        },
        {
            "label":     _("Suggested"),
            "fieldname": "suggest_payment",
            "fieldtype": "Check",
            "width":     90,
        },
        {
            "label":     _("Withholding Type"),
            "fieldname": "withholding_type_display",
            "fieldtype": "Data",
            "width":     110,
        },
        {
            "label":     _("Tax Rate (%)"),
            "fieldname": "tax_rate",
            "fieldtype": "Float",
            "precision": 2,
            "width":     90,
        },
        {
            "label":     _("KRA PIN"),
            "fieldname": "tax_id",
            "fieldtype": "Data",
            "width":     140,
        },
        {
            "label":     _("Supplier Invoice No"),
            "fieldname": "bill_no",
            "fieldtype": "Data",
            "width":     150,
        },
        {
            "label":     _("Invoice Date"),
            "fieldname": "bill_date",
            "fieldtype": "Date",
            "width":     110,
        },
        {
            "label":     _("Supplier"),
            "fieldname": "supplier",
            "fieldtype": "Link",
            "options":   "Supplier",
            "width":     240,
        },
        {
            "label":     _("Nature of Transaction"),
            "fieldname": "nature_of_transaction",
            "fieldtype": "Data",
            "width":     260,
        },
        {
            "label":     _("Vatable Amount (Transaction Currency)"),
            "fieldname": "base_amount",
            "fieldtype": "Currency",
            "options":   "transaction_currency",
            "width":     200,
        },
        {
            "label":     _("Withheld Amount (Transaction Currency)"),
            "fieldname": "withheld_amount_transaction",
            "fieldtype": "Currency",
            "options":   "transaction_currency",
            "width":     200,
        },
        {
            "label":     _("Transaction Currency"),
            "fieldname": "transaction_currency",
            "fieldtype": "Link",
            "options":   "Currency",
            "width":     90,
        },
        {
            "label":     _("Exchange Rate"),
            "fieldname": "exchange_rate",
            "fieldtype": "Float",
            "precision": 6,
            "width":     110,
        },
        {
            "label":     _("Vatable Amount (KES)"),
            "fieldname": "base_net_amount",
            "fieldtype": "Currency",
            "width":     160,
        },
        {
            "label":     _("Withheld Amount (KES)"),
            "fieldname": "withheld_amount",
            "fieldtype": "Currency",
            "width":     160,
        },
        {
            "label":     _("System Invoice No"),
            "fieldname": "invoice_number",
            "fieldtype": "Link",
            "options":   "Purchase Invoice",
            "width":     180,
        },
        {
            "label":     _("Status"),
            "fieldname": "payment_status",
            "fieldtype": "Data",
            "width":     90,
        },
        {
            "label":     _("Payment Date"),
            "fieldname": "payment_date",
            "fieldtype": "Date",
            "width":     110,
        },
        {
            "label":     _("PRN Number"),
            "fieldname": "prn_number",
            "fieldtype": "Data",
            "width":     120,
        },
        {
            "label":     _("Journal Entry"),
            "fieldname": "journal_entry",
            "fieldtype": "Link",
            "options":   "Journal Entry",
            "width":     180,
        },
    ]


# ---------------------------------------------------------------------------
# Resolve withholding accounts via tax_report_type tag
# ---------------------------------------------------------------------------

def get_withholding_accounts(company, report_types=("WHTAX", "WHVAT")):
    """
    Return { account_name: tax_report_type } for all accounts tagged as
    WHTAX or WHVAT via the is_tax_report_account / tax_report_type fields.

    Falls back to an empty dict if no accounts are tagged — the caller
    will warn the user.
    """
    placeholders = ", ".join(["%s"] * len(report_types))
    sql = """
        SELECT name, tax_report_type
        FROM   `tabAccount`
        WHERE  account_type         = 'Tax'
          AND  is_tax_report_account = 1
          AND  tax_report_type       IN ({ph})
          {company_cond}
    """.format(
        ph=placeholders,
        company_cond="AND company = %s" if company else "",
    )
    params = list(report_types)
    if company:
        params.append(company)

    rows = frappe.db.sql(sql, tuple(params), as_dict=True)
    return {r.name: r.tax_report_type for r in rows}


# ---------------------------------------------------------------------------
# Pull nature_of_transaction from Tax Withholding Category
# ---------------------------------------------------------------------------

def get_nature_map():
    """
    Returns { tax_withholding_category_name: nature_of_transaction }
    from the custom field we added to Tax Withholding Category.
    """
    rows = frappe.get_all(
        "Tax Withholding Category",
        fields=["name", "nature_of_transaction"],
    )
    return {r.name: r.nature_of_transaction or "" for r in rows}


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters):
    company = filters.get("company")

    # Determine which report types to include based on withholding_type filter
    if filters.get("withholding_type") == "WHTAX":
        report_types = ("WHTAX",)
    elif filters.get("withholding_type") == "WHVAT":
        report_types = ("WHVAT",)
    else:
        report_types = ("WHTAX", "WHVAT")

    wh_accounts = get_withholding_accounts(company, report_types)

    if not wh_accounts:
        frappe.msgprint(
            _(
                "No withholding accounts are tagged for WHTAX or WHVAT. "
                "Please open the relevant Tax accounts, enable "
                "<b>Include in Tax Report</b> and set <b>Tax Report</b> "
                "to <b>WHTAX</b> or <b>WHVAT</b>."
            ),
            indicator="orange",
            title=_("No Withholding Accounts Found"),
        )
        return []

    # Apply optional single-account filter
    if filters.get("withholding_account"):
        acct = filters["withholding_account"]
        if acct in wh_accounts:
            wh_accounts = {acct: wh_accounts[acct]}
        else:
            frappe.msgprint(
                _("Selected account is not tagged as a withholding account."),
                indicator="orange",
            )
            return []

    nature_map = get_nature_map()

    acc_ph = ", ".join(["%s"] * len(wh_accounts))

    conditions, params = build_conditions(filters)

    sql = """
        SELECT
            COALESCE(wtp.name,
                CONCAT('temp_', pi.name, '_', pit.account_head)
            )                                               AS name,
            pi.name                                         AS invoice_number,
            pi.bill_no,
            pi.bill_date,
            pi.supplier,
            pi.tax_withholding_category,
            pi.tax_withholding_net_total                    AS base_amount,
            pi.base_tax_withholding_net_total               AS base_net_amount,
            pi.currency                                     AS transaction_currency,
            pi.conversion_rate                              AS exchange_rate,
            sup.tax_id,
            sup.country                                     AS supplier_country,
            pit.account_head                                AS withholding_account,
            pit.base_tax_amount_after_discount_amount       AS withheld_amount,
            pit.tax_amount                                  AS withheld_amount_transaction,
            pit.rate                                        AS tax_rate,
            wtp.name                                        AS wtp_name,
            wtp.payment_status,
            wtp.payment_date,
            wtp.prn_number,
            wtp.journal_entry,
            COALESCE(wtp.custom_suggestion, 0)              AS suggest_payment
        FROM `tabPurchase Invoice` pi
        JOIN `tabPurchase Taxes and Charges` pit
            ON  pit.parent      = pi.name
            AND pit.account_head IN ({acc_ph})
            AND pit.tax_amount  > 0
        LEFT JOIN `tabSupplier` sup
            ON sup.name = pi.supplier
        LEFT JOIN `tabWithholding Tax Payment` wtp
            ON  wtp.purchase_invoice    = pi.name
            AND wtp.withholding_account = pit.account_head
        WHERE pi.docstatus = 1
        {conditions}
        ORDER BY pi.bill_date DESC, pi.name DESC
    """.format(
        acc_ph=acc_ph,
        conditions=conditions,
    )

    all_params = list(wh_accounts.keys()) + params
    rows = frappe.db.sql(sql, tuple(all_params), as_dict=True)

    for row in rows:
        # Payment status default
        if not row.get("payment_status"):
            row["payment_status"] = "Unpaid"

        # Withholding type display label from account tag
        report_type = wh_accounts.get(row.get("withholding_account"), "")
        row["withholding_type_display"] = report_type   # WHTAX or WHVAT
        row["withholding_type"]         = report_type

        # Nature of transaction from Tax Withholding Category
        twc = row.get("tax_withholding_category") or ""
        row["nature_of_transaction"] = nature_map.get(twc, "")

        # Residential status derived from supplier country
        country = row.get("supplier_country") or ""
        row["residential_status"] = "Resident" if country.strip().lower() == "kenya" else "Non Resident"
        row["country"] = country

        # WTP record name for JS
        row["wtp_record_name"] = row.get("wtp_name") or None

        # suggest_payment as int for JS truthiness
        row["suggest_payment"] = int(row.get("suggest_payment") or 0)

        # Default select_row
        row["select_row"] = 0

        # Normalise numerics
        row["exchange_rate"] = flt(row.get("exchange_rate") or 1.0, 6)
        row["tax_rate"]      = flt(row.get("tax_rate") or 0.0, 2)

        # KES fallback for transaction currency amounts
        if not row.get("withheld_amount_transaction"):
            row["withheld_amount_transaction"] = row.get("withheld_amount", 0)

    return rows


# ---------------------------------------------------------------------------
# Conditions builder
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

    if filters.get("payment_status"):
        if filters["payment_status"] == "Paid":
            conditions.append("wtp.payment_status = 'Paid'")
        else:
            conditions.append(
                "(wtp.payment_status IS NULL OR wtp.payment_status != 'Paid')"
            )

    cond_str = ("AND " + " AND ".join(conditions)) if conditions else ""
    return cond_str, params


# ---------------------------------------------------------------------------
# Whitelisted API methods (unchanged from original, kept here)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def update_suggestion_flag(wtp_name, value):
    if not wtp_name:
        frappe.throw(_("Withholding Tax Payment name is required"))
    try:
        doc = frappe.get_doc("Withholding Tax Payment", wtp_name)
        doc.custom_suggestion = int(value)
        doc.save(ignore_permissions=False)
        frappe.db.commit()
        return {"status": "success", "wtp_name": wtp_name, "value": int(value)}
    except frappe.DoesNotExistError:
        frappe.throw(_("Record not found: {0}").format(wtp_name))
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Suggestion Flag Update Error")
        frappe.throw(_("Error: {0}").format(str(e)))


@frappe.whitelist()
def process_withholding_payments(
    selected_rows, bank_account,
    reference_number=None, reference_date=None, user_remark=None
):
    import json
    if isinstance(selected_rows, str):
        selected_rows = json.loads(selected_rows)
    if not selected_rows:
        frappe.throw(_("Please select at least one row"))
    if not bank_account:
        frappe.throw(_("Please select a bank account"))

    bank_currency = frappe.db.get_value("Account", bank_account, "account_currency")
    if bank_currency != "KES":
        frappe.throw(_("Please select a KES bank account"))

    je = _create_batch_journal(
        selected_rows, bank_account, reference_number, reference_date, user_remark
    )
    for row in selected_rows:
        _create_or_update_wtp(row, je.name)

    return {
        "status":             "success",
        "journal_entry":      je.name,
        "processed_invoices": len(selected_rows),
        "message": _("Successfully processed {0} payment(s)").format(len(selected_rows)),
    }


def _create_batch_journal(
    selected_rows, bank_account,
    reference_number=None, reference_date=None, user_remark=None
):
    company      = frappe.db.get_value("Account", bank_account, "company")
    account_totals = defaultdict(float)
    invoice_list   = []

    for row in selected_rows:
        acct   = row.get("withholding_account")
        amount = flt(row.get("withheld_amount", 0))
        if acct:
            account_totals[acct] += amount
        if row.get("bill_no"):
            invoice_list.append(str(row["bill_no"]))

    total = flt(sum(account_totals.values()))
    if total <= 0:
        frappe.throw(_("Total withholding amount must be greater than 0"))

    je = frappe.new_doc("Journal Entry")
    je.voucher_type  = "Excise Entry"
    je.company       = company
    je.posting_date  = getdate(reference_date) if reference_date else getdate(nowdate())

    summary = ", ".join(invoice_list[:5])
    if len(invoice_list) > 5:
        summary += " and {0} more".format(len(invoice_list) - 5)
    base_remark = "Batch withholding tax payment for invoices: {0}".format(summary)
    je.user_remark = "{0}\n\nRemarks: {1}".format(base_remark, user_remark) if user_remark else base_remark

    if reference_number:
        je.cheque_no   = str(reference_number)
        je.cheque_date = getdate(reference_date) if reference_date else getdate(nowdate())

    je.append("accounts", {
        "account":                    bank_account,
        "credit_in_account_currency": total,
        "user_remark":                "Batch withholding tax payment",
    })
    for acct, amount in account_totals.items():
        je.append("accounts", {
            "account":                   acct,
            "debit_in_account_currency": flt(amount),
        })

    je.insert()
    je.submit()
    return je


def _create_or_update_wtp(row_data, journal_entry):
    invoice_number      = row_data.get("invoice_number")
    withholding_account = row_data.get("withholding_account")
    if not invoice_number or not withholding_account:
        return

    existing = frappe.db.exists("Withholding Tax Payment", {
        "purchase_invoice":   invoice_number,
        "withholding_account": withholding_account,
    })

    if existing:
        wtp = frappe.get_doc("Withholding Tax Payment", existing)
    else:
        wtp = frappe.new_doc("Withholding Tax Payment")
        wtp.purchase_invoice    = invoice_number
        wtp.withholding_account = withholding_account
        wtp.supplier            = row_data.get("supplier")
        wtp.withheld_amount     = flt(row_data.get("withheld_amount", 0))

    wtp.payment_status = "Paid"
    wtp.payment_date   = nowdate()
    wtp.journal_entry  = journal_entry
    wtp.save() if existing else wtp.insert()


@frappe.whitelist()
def batch_update_prn_numbers(prn_updates):
    import json
    if isinstance(prn_updates, str):
        prn_updates = json.loads(prn_updates)
    if not prn_updates:
        frappe.throw(_("No PRN numbers to update"))

    updated = 0
    errors  = []
    for upd in prn_updates:
        try:
            wtp_name   = upd.get("name")
            prn_number = (upd.get("prn_number") or "").strip()
            if not wtp_name or not prn_number:
                continue
            wtp = frappe.get_doc("Withholding Tax Payment", wtp_name)
            if wtp.payment_status != "Paid":
                errors.append("{0} is not Paid".format(wtp_name))
                continue
            wtp.prn_number = prn_number
            wtp.save()
            updated += 1
        except Exception as e:
            errors.append(str(e))
            frappe.log_error(frappe.get_traceback(), "PRN Update Error")

    msg = "Updated {0} PRN number(s)".format(updated)
    if errors:
        msg += "\nErrors: " + ", ".join(errors[:3])
    return {
        "status":        "success" if updated > 0 else "partial",
        "updated_count": updated,
        "message":       msg,
    }


# ---------------------------------------------------------------------------
# Hook — auto-create WTP record on Purchase Invoice submit
# ---------------------------------------------------------------------------

def create_unpaid_wtp_on_submit(doc, method=None):
    """
    Called from hooks.py on Purchase Invoice submit.
    Creates a Withholding Tax Payment record for each withholding line
    using tagged accounts instead of LIKE patterns.
    """
    company = doc.company
    wh_accounts = get_withholding_accounts(company)
    if not wh_accounts:
        return

    lines = frappe.db.sql("""
        SELECT account_head, base_tax_amount_after_discount_amount, tax_amount, rate
        FROM   `tabPurchase Taxes and Charges`
        WHERE  parent      = %s
          AND  account_head IN ({ph})
          AND  tax_amount  > 0
    """.format(ph=", ".join(["%s"] * len(wh_accounts))),
        tuple([doc.name] + list(wh_accounts.keys())),
        as_dict=True,
    )

    for line in lines:
        if frappe.db.exists("Withholding Tax Payment", {
            "purchase_invoice":   doc.name,
            "withholding_account": line.account_head,
        }):
            continue
        try:
            wtp = frappe.new_doc("Withholding Tax Payment")
            wtp.purchase_invoice    = doc.name
            wtp.withholding_account = line.account_head
            wtp.supplier            = doc.supplier
            wtp.withheld_amount     = flt(line.base_tax_amount_after_discount_amount, 2)
            wtp.payment_status      = "Unpaid"
            wtp.insert(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(
                frappe.get_traceback(),
                "Failed to create WTP on submit: {0}".format(str(e)),
            )


def cancel_wtp_on_invoice_cancel(doc, method=None):
    unpaid = frappe.get_all(
        "Withholding Tax Payment",
        filters={"purchase_invoice": doc.name, "payment_status": "Unpaid"},
        fields=["name"],
    )
    for r in unpaid:
        try:
            frappe.delete_doc(
                "Withholding Tax Payment", r.name,
                ignore_permissions=True, force=True,
            )
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Failed to delete WTP on cancel")
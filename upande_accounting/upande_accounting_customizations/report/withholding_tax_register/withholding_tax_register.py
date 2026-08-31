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

from upande_accounting.withholding_tax_management import (
    get_withholding_accounts,
    resolve_withholding_category,
)
from upande_accounting.utils import category_base_from_item_rows, WITHHOLDING_OVERRIDE_FIELDS


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
            "label":     _("Invoice Paid"),
            "fieldname": "invoice_paid",
            "fieldtype": "Data",
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
            "label":     _("Taxable Amount (Transaction Currency)"),
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
            "label":     _("Taxable Amount (KES)"),
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
# Withholding account / category resolution now lives in
# upande_accounting.withholding_tax_management (imported above) — shared with
# the Purchase Invoice submit/cancel hooks so the register and the Withholding
# Tax Management framework always agree on which accounts and categories
# apply.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-invoice category / taxable-base resolution
#
# A withholding account is often shared by several Tax Withholding Categories
# (different rates over time, goods vs. services) — resolve_withholding_category
# restricts the match to the categories actually selected on the invoice
# (tax_withholding_category + custom_withholding_1/2/3).
#
# The taxable base for a row is NOT the invoice's overall gross/net total:
#   - Service-only categories → net_amount summed over items flagged
#     custom_is_service_item=1.
#   - All-item categories      → net_amount summed over items flagged
#     apply_tds=1.
# An item may override this default per category via custom_override_withholding /
# custom_withholding_action / custom_withholding_override_category (see
# upande_accounting.utils.category_base_from_item_rows) — the same function that
# calculates the actual withholding on the invoice, so this report can never disagree
# with what was really withheld.
# Falls back to the invoice's own tax_withholding_net_total when no category
# can be matched (legacy/manually edited rows).
# ---------------------------------------------------------------------------

def _get_invoice_items(invoice_numbers):
    """Per-invoice item rows, with the fields category_base_from_item_rows needs."""
    if not invoice_numbers:
        return {}

    rows = frappe.get_all(
        "Purchase Invoice Item",
        filters={"parent": ["in", invoice_numbers]},
        fields=["parent", "net_amount", "base_net_amount", *WITHHOLDING_OVERRIDE_FIELDS],
    )
    items_by_invoice = defaultdict(list)
    for r in rows:
        items_by_invoice[r.parent].append(r)
    return items_by_invoice


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

    acc_ph = ", ".join(["%s"] * len(wh_accounts))

    conditions, params = build_conditions(filters)

    sql = """
        SELECT
            COALESCE(wtm.name,
                CONCAT('temp_', pi.name, '_', pit.account_head)
            )                                               AS name,
            pi.name                                         AS invoice_number,
            pi.bill_no,
            pi.bill_date,
            pi.company,
            pi.supplier,
            pi.tax_withholding_category,
            pi.custom_withholding_1,
            pi.custom_withholding_2,
            pi.custom_withholding_3,
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
            wtm.name                                        AS wtp_name,
            wtm.payment_status,
            wtm.payment_date,
            wtm.prn_number,
            wtm.journal_entry,
            COALESCE(wtm.suggested_for_payment, 0)          AS invoice_paid
        FROM `tabPurchase Invoice` pi
        JOIN `tabPurchase Taxes and Charges` pit
            ON  pit.parent      = pi.name
            AND pit.account_head IN ({acc_ph})
            AND pit.tax_amount  > 0
        LEFT JOIN `tabSupplier` sup
            ON sup.name = pi.supplier
        LEFT JOIN `tabWithholding Tax Management` wtm
            ON  wtm.purchase_invoice    = pi.name
            AND wtm.withholding_account = pit.account_head
        WHERE pi.docstatus = 1
        {conditions}
        ORDER BY pi.bill_date DESC, pi.name DESC
    """.format(
        acc_ph=acc_ph,
        conditions=conditions,
    )

    all_params = list(wh_accounts.keys()) + params
    rows = frappe.db.sql(sql, tuple(all_params), as_dict=True)
    if not rows:
        return []

    # Resolve the category actually selected on each invoice for this account
    # (bulk — a per-row lookup would mean N extra queries for N rows). Only
    # the categories selected on the invoice itself are considered, since one
    # withholding account is often shared by several categories.
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
            fields=["name", "nature_of_transaction", "custom_applicable_for_services"],
        ):
            category_meta[r.name] = r

    items_by_invoice = _get_invoice_items(list(invoice_categories.keys()))

    for row in rows:
        # Payment status default
        if not row.get("payment_status"):
            row["payment_status"] = "Unpaid"

        # Withholding type display label from account tag
        report_type = wh_accounts.get(row.get("withholding_account"), "")
        row["withholding_type_display"] = report_type   # WHTAX or WHVAT
        row["withholding_type"]         = report_type

        matched_category = None
        for cat in invoice_categories.get(row["invoice_number"], []):
            if row["withholding_account"] in accounts_by_category.get(cat, set()):
                matched_category = cat
                break
        meta = category_meta.get(matched_category)
        row["nature_of_transaction"] = (meta.nature_of_transaction if meta else None) or ""

        # Taxable amount = the actual item-level base the rate was applied to,
        # not the invoice's overall gross/net total. Only computed when a category
        # could actually be matched — otherwise there's nothing to key the per-item
        # override fields off of, so keep the SQL fallback below.
        is_service_only = bool(meta and meta.custom_applicable_for_services)
        if matched_category:
            invoice_items = items_by_invoice.get(row["invoice_number"], [])
            base_amount = category_base_from_item_rows(
                invoice_items, matched_category, is_service_only, amount_field="net_amount"
            )
            base_net_amount = category_base_from_item_rows(
                invoice_items, matched_category, is_service_only, amount_field="base_net_amount"
            )
            if base_amount:
                row["base_amount"] = base_amount
            if base_net_amount:
                row["base_net_amount"] = base_net_amount
        # else: keep the SQL fallback (tax_withholding_net_total) already on the row

        # Residential status derived from supplier country
        country = row.get("supplier_country") or ""
        row["residential_status"] = "Resident" if country.strip().lower() == "kenya" else "Non Resident"
        row["country"] = country

        # WTP record name for JS
        row["wtp_record_name"] = row.get("wtp_name") or None

        # invoice_paid as int for JS truthiness
        row["invoice_paid"] = int(row.get("invoice_paid") or 0)

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

    existing = frappe.db.exists("Withholding Tax Management", {
        "purchase_invoice":   invoice_number,
        "withholding_account": withholding_account,
    })

    if existing:
        wtp = frappe.get_doc("Withholding Tax Management", existing)
    else:
        wtp = frappe.new_doc("Withholding Tax Management")
        wtp.purchase_invoice      = invoice_number
        wtp.withholding_account   = withholding_account
        wtp.supplier              = row_data.get("supplier")
        wtp.withheld_amount       = flt(row_data.get("withheld_amount", 0))
        pi_doc = frappe.get_doc("Purchase Invoice", invoice_number)
        wtp.withholding_category  = resolve_withholding_category(pi_doc, withholding_account, pi_doc.company)

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
            wtp = frappe.get_doc("Withholding Tax Management", wtp_name)
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
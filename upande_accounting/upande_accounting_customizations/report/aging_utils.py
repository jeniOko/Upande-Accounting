# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Shared helpers for the Debtors / Creditors Aging reports and their summaries.

  1. group_rows  — the "Group By" filter: re-orders report rows under a bold
     header per group, followed by a bold sub-total row. Sub-totals are split
     per currency inside a group, so amounts in different party currencies
     are never added together.

  2. set_advance_in_party_currency  — ERPNext's summary reports always take
     the Advance Amount from Payment Ledger Entry.amount (company currency),
     even with In Party Currency checked. This re-derives it from
     amount_in_account_currency, i.e. the currency of the party's own
     receivable/payable account (its billing currency).

  3. sort_by_party  — every report lists parties A to Z by name, grouped or not.
"""

from collections import OrderedDict
from datetime import date

import frappe
from frappe import _
from frappe.query_builder.functions import Abs, Date, Sum
from frappe.utils import flt, getdate

GROUP_BY_FIELDS = {
    "Group by Currency": "currency",
    # Detailed views only — a summary already has one row per party
    "Group by Customer": "party",
    "Group by Supplier": "party",
    "Group by Customer Group": "customer_group",
    "Group by Supplier Group": "supplier_group",
    "Group by Territory": "territory",
}


def sort_by_party(data, party_doctype, date_fields=()):
    """
    Sort rows A to Z by the party's name (customer_name / supplier_name,
    falling back to the ID). With date_fields (detailed views), rows are
    ordered oldest first by those dates and the party name only breaks ties;
    a row missing a date field (e.g. a Payment Entry has no bill_date) uses
    its posting_date. Runs before group_rows, which keeps this order inside
    each group.
    """
    name_field = "customer_name" if party_doctype == "Customer" else "supplier_name"
    parties = list({row.get("party") for row in data if row and row.get("party")})
    names = dict(
        frappe.get_all(party_doctype, filters={"name": ("in", parties)}, fields=["name", name_field], as_list=1)
    ) if parties else {}

    def key(row):
        dates = [getdate(row.get(f) or row.get("posting_date") or date.min) for f in date_fields]
        return (*dates, (names.get(row.get("party")) or row.get("party") or "").casefold())

    data.sort(key=key)
    return data


def add_party_name_column(columns, data, party_doctype):
    """
    Fill party_name on every party row and show a Customer / Supplier Name
    column right after Party whenever any party's ID differs from its name.
    ERPNext only adds that column when Selling / Buying Settings say "Naming
    Series", but parties can be series-named regardless of that setting
    (e.g. CUS00013), and the A to Z order by name is then unreadable.
    """
    name_field = "customer_name" if party_doctype == "Customer" else "supplier_name"
    rows = [row for row in data if row and row.get("party") and not row.get("bold")]
    parties = list({row.party for row in rows})
    names = dict(
        frappe.get_all(party_doctype, filters={"name": ("in", parties)}, fields=["name", name_field], as_list=1)
    ) if parties else {}

    for row in rows:
        row.party_name = names.get(row.party) or row.get("party_name") or row.party

    if any(row.party_name != row.party for row in rows) and not any(
        c["fieldname"] == "party_name" for c in columns
    ):
        idx = next(i for i, c in enumerate(columns) if c["fieldname"] == "party") + 1
        columns.insert(idx, dict(
            label=_("{0} Name").format(_(party_doctype)), fieldname="party_name", fieldtype="Data", width=250,
        ))


def is_grouped(filters):
    return bool(GROUP_BY_FIELDS.get(filters.get("group_by")))


def group_rows(data, columns, filters, label_field="party", group_by_fields=GROUP_BY_FIELDS):
    """
    Return `data` re-ordered by the Group By filter's field (looked up in
    group_by_fields, so other reports can bring their own), each group as:
    header row, its rows (original order kept), one sub-total row per
    currency, blank spacer. Rows already marked bold (base-report sub-totals)
    are dropped so nothing is counted twice. A Grand Total row is added only
    when every row shares one currency.
    """
    fieldname = group_by_fields.get(filters.get("group_by"))
    if not fieldname or not data:
        return data

    amount_fields = [c["fieldname"] for c in columns if c.get("fieldtype") == "Currency"]
    not_set = _("Not Set")

    groups = OrderedDict()
    labels = {}
    for row in data:
        if not row or row.get("bold"):
            continue
        key = row.get(fieldname) or not_set
        groups.setdefault(key, []).append(row)
        if fieldname == "party":
            # Party groups are shown and ordered by name, not ID
            labels[key] = (
                row.get("customer_name") or row.get("supplier_name") or row.get("party_name") or key
            )

    out = []
    grand_totals = OrderedDict()
    for key in sorted(groups, key=lambda k: (k == not_set, labels.get(k, k).casefold())):
        rows = groups[key]
        key = labels.get(key, key)
        out.append({label_field: key, "bold": 1, "is_group_header": 1})
        out.extend(rows)

        totals = OrderedDict()
        for row in rows:
            currency = row.get("currency")
            for bucket in (totals, grand_totals):
                total = bucket.setdefault(currency, {"currency": currency, "bold": 1})
                for f in amount_fields:
                    total[f] = flt(total.get(f)) + flt(row.get(f))

        for currency, total in totals.items():
            if len(totals) > 1:
                total[label_field] = _("Total {0} ({1})").format(key, currency)
            else:
                total[label_field] = _("Total {0}").format(key)
            out.append(total)
        out.append({})

    if len(grand_totals) == 1:
        grand_total = next(iter(grand_totals.values()))
        grand_total[label_field] = _("Grand Total")
        out.append(grand_total)

    return out


def get_partywise_advance_in_account_currency(party_type, posting_date, company, future_payment=0):
    """
    Mirror of erpnext.accounts.party.get_partywise_advanced_payment_amount,
    summing amount_in_account_currency instead of amount (company currency).
    """
    account_type = frappe.get_cached_value("Party Type", party_type[0], "account_type")

    ple = frappe.qb.DocType("Payment Ledger Entry")
    acc = frappe.qb.DocType("Account")

    query = (
        frappe.qb.from_(ple)
        .inner_join(acc)
        .on(ple.account == acc.name)
        .select(ple.party)
        .where(
            (ple.party_type.isin(party_type))
            & (acc.account_type == account_type)
            & (ple.delinked == 0)
            & (ple.company == company)
        )
        .groupby(ple.party)
    )

    if future_payment:
        query = query.where((ple.posting_date <= posting_date) | (Date(ple.creation) <= posting_date))
    else:
        query = query.where(ple.posting_date <= posting_date)

    if invoice_doctypes := frappe.get_hooks("invoice_doctypes"):
        query = query.where(ple.voucher_type.notin(invoice_doctypes))

    # Unallocated payments sitting on the Receivable / Payable account
    party_ledger = (
        query.select(Abs(Sum(ple.amount_in_account_currency)))
        .where(ple.amount_in_account_currency < 0)
        .where(ple.against_voucher_no == ple.voucher_no)
        .where(acc.root_type == ("Liability" if account_type == "Payable" else "Asset"))
    )
    data = frappe._dict(party_ledger.run() or {})

    # Payments booked to a separate Advance account
    advance_ledger = (
        query.select(Sum(ple.amount_in_account_currency), ple.account)
        .where(acc.root_type == ("Asset" if account_type == "Payable" else "Liability"))
        .groupby(ple.account)
        .having(Sum(ple.amount_in_account_currency) < 0)
    )
    for row in advance_ledger.run():
        data[row[0]] = flt(data.get(row[0])) + abs(row[1])

    return data


def set_advance_in_party_currency(data, party_type, filters):
    """
    Replace the summary's company-currency Advance Amount with the party
    currency one. The base report subtracted the old advance from paid, so
    swap it there too; outstanding and the ageing buckets are already in party
    currency and stay as they are.
    """
    advances = get_partywise_advance_in_account_currency(
        party_type, filters.report_date, filters.company, filters.get("show_future_payments")
    )
    for row in data:
        advance = flt(advances.get(row.party))
        row.paid = flt(row.paid) + flt(row.advance) - advance
        row.advance = advance

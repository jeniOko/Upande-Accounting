# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Advance Payment Register — every advance Payment Entry (customer or supplier),
what it was paid for, and how much of it is still open.

One row per Payment Entry.

A submitted Payment Entry counts as an ADVANCE if ANY of these is true:
  1. It references a Sales Order or Purchase Order — directly, or through an
     invoice reference whose advance_voucher_no is an order (ERPNext moves
     an order advance onto the invoice when the invoice pulls it in).
  2. It has no references at all (paid / received on account).
  3. It still carries unallocated_amount > 0.

ALLOCATION is measured against invoices only: an advance linked to an order
but not yet invoiced is still fully open, so the "paid but not delivered"
advances show up in the default Unallocated / Partially Allocated view.
  Open Balance = unallocated_amount + amount allocated to orders

GOODS / SERVICES / ASSETS comes from the linked orders' / invoices' items:
Item.is_fixed_asset -> Assets, Item.custom_is_service_item -> Services,
anything else -> Goods. An advance can cover several (e.g. "Goods & Assets");
the filter matches an advance that includes ANY of the chosen kinds. An
advance with nothing linked is "Not Specified".

DELIVERY STATUS comes from the linked orders, weighted by item amount:
goods and assets count as delivered by delivered / received qty, services by
billed amount (a service is rarely "received" on a receipt, it is billed).

All amounts are in the party's account currency (paid_amount for Receive,
received_amount for Pay). The chart and summary cards are in company
currency so different currencies can be added up.
"""

from collections import OrderedDict

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, today

from upande_accounting.upande_accounting_customizations.report.aging_utils import group_rows

ORDER_DOCTYPES = ("Sales Order", "Purchase Order")
INVOICE_DOCTYPES = ("Sales Invoice", "Purchase Invoice")

ALLOCATION_STATUSES = ("Unallocated", "Partially Allocated", "Fully Allocated")
DEFAULT_ALLOCATION_STATUSES = ["Unallocated", "Partially Allocated"]

LINKED_TO_ORDER = "Sales / Purchase Order"
LINKED_TO_INVOICE = "Invoice Only"
LINKED_TO_NOTHING = "Nothing Yet"

GOODS, SERVICES, ASSETS, NOT_SPECIFIED = "Goods", "Services", "Assets", "Not Specified"
NATURES = (GOODS, SERVICES, ASSETS)

NOT_DELIVERED, PARTLY_DELIVERED, FULLY_DELIVERED = "Not Delivered", "Partly Delivered", "Fully Delivered"
INVOICED, NOT_LINKED = "Invoiced", "Not Linked"
PENDING_DELIVERY = (NOT_DELIVERED, PARTLY_DELIVERED)

GROUP_BY_FIELDS = {
    "Group by Currency": "currency",
    "Group by Party Type": "party_type",
    "Group by Party": "party",
    "Group by Goods / Services / Assets": "advance_for",
    "Group by Delivery Status": "delivery_status",
    "Group by Allocation Status": "allocation_status",
    "Group by Advance Linked To": "linked_to",
}

AGE_BUCKETS = ((30, "0-30"), (60, "31-60"), (90, "61-90"), (None, "90+"))


def execute(filters=None):
    filters = frappe._dict(filters or {})
    # Never touched by the user (vs. cleared, which comes through as []):
    # fall back to the same default the JS filter sets.
    if filters.get("allocation_status") is None:
        filters.allocation_status = DEFAULT_ALLOCATION_STATUSES

    company_currency = frappe.get_cached_value("Company", filters.company, "default_currency")
    advances = [a for a in get_advances(filters) if matches_filters(a, filters)]

    data = [adv.row for adv in advances]
    columns = get_columns(data)
    # Header / sub-total rows sit in the Party column; the chart and cards
    # below are built from `advances`, so they never see them.
    data = group_rows(data, columns, filters, label_field="party", group_by_fields=GROUP_BY_FIELDS)

    return (
        columns,
        data,
        None,
        get_chart(advances, company_currency),
        get_report_summary(advances, company_currency),
    )


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns(data):
    def col(label, fieldname, fieldtype="Data", width=120, options=None):
        return {"label": _(label), "fieldname": fieldname, "fieldtype": fieldtype, "options": options, "width": width}

    columns = [
        col("Advance Payment", "payment_entry", "Link", 220, "Payment Entry"),
        col("Payment Date", "posting_date", "Date", 110),
        col("Party Type", "party_type", width=95),
        col("Party", "party", "Dynamic Link", 160, "party_type"),
    ]
    # Party Name only adds anything when parties are named by code
    # (e.g. SUP00006); where the ID is the name it would repeat Party.
    if any(row.party_name and row.party_name != row.party for row in data):
        columns.append(col("Party Name", "party_name", width=280))

    return columns + [
        col("Currency", "currency", "Link", 75, "Currency"),
        col("Advance Amount", "advance_amount", "Currency", 135, "currency"),
        col("Linked To Order", "order_allocated", "Currency", 135, "currency"),
        col("Allocated To Invoices", "invoice_allocated", "Currency", 150, "currency"),
        col("Open Balance", "open_balance", "Currency", 135, "currency"),
        col("Goods / Services / Assets", "advance_for", width=170),
        col("Delivered %", "fulfilled_percent", "Percent", 100),
        col("Delivery Status", "delivery_status", width=130),
        col("Allocation Status", "allocation_status", width=140),
        col("Advance Linked To", "linked_to", width=160),
    ]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_advances(filters):
    payments = get_payment_entries(filters)
    if not payments:
        return []

    refs_by_pe = OrderedDict()
    for ref in frappe.get_all(
        "Payment Entry Reference",
        filters={"parent": ("in", [p.name for p in payments])},
        fields=["parent", "reference_doctype", "reference_name", "allocated_amount",
                "advance_voucher_type", "advance_voucher_no"],
        order_by="idx",
    ):
        refs_by_pe.setdefault(ref.parent, []).append(ref)

    docs = get_linked_document_details(refs_by_pe)

    advances = []
    for pe in payments:
        adv = build_advance(pe, refs_by_pe.get(pe.name, []), docs)
        if adv:
            advances.append(adv)
    return advances


def as_list(value):
    """A MultiSelectList filter's value — a list, or a lone string if set by hand."""
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def get_payment_entries(filters):
    values = {k: filters.get(k) for k in ("company", "from_date", "to_date", "party_type")}
    conditions = ["pe.docstatus = 1", "pe.payment_type IN ('Receive', 'Pay')", "pe.company = %(company)s"]
    if filters.get("from_date"):
        conditions.append("pe.posting_date >= %(from_date)s")
    if filters.get("to_date"):
        conditions.append("pe.posting_date <= %(to_date)s")
    if filters.get("party_type"):
        conditions.append("pe.party_type = %(party_type)s")
    if filters.get("party"):
        conditions.append("pe.party IN %(party)s")
        values["party"] = tuple(as_list(filters.party))

    return frappe.db.sql(
        f"""
        SELECT
            pe.name, pe.posting_date, pe.payment_type, pe.party_type, pe.party, pe.party_name,
            pe.unallocated_amount, pe.total_allocated_amount,
            -- The party's side of the payment: paid_from for money received
            -- from a customer, paid_to for money paid to a supplier.
            IF(pe.payment_type = 'Receive', pe.paid_from_account_currency,
                                            pe.paid_to_account_currency)  AS currency,
            IF(pe.payment_type = 'Receive', pe.paid_amount, pe.received_amount) AS advance_amount,
            IF(pe.payment_type = 'Receive', pe.source_exchange_rate,
                                            pe.target_exchange_rate)      AS exchange_rate
        FROM `tabPayment Entry` pe
        WHERE {" AND ".join(conditions)}
        ORDER BY pe.posting_date DESC, pe.name DESC
        """,
        values,
        as_dict=True,
    )


def get_linked_document_details(refs_by_pe):
    """
    {(doctype, name): {date, grand_total, status, natures, fulfilled_percent}}
    for every order and invoice any of the payments point to.
    """
    names = {dt: set() for dt in ORDER_DOCTYPES + INVOICE_DOCTYPES}
    for refs in refs_by_pe.values():
        for ref in refs:
            if ref.reference_doctype in names:
                names[ref.reference_doctype].add(ref.reference_name)
            if ref.advance_voucher_type in ORDER_DOCTYPES and ref.advance_voucher_no:
                names[ref.advance_voucher_type].add(ref.advance_voucher_no)

    docs = {}
    for doctype, doc_names in names.items():
        if not doc_names:
            continue
        is_order = doctype in ORDER_DOCTYPES
        date_field = "transaction_date" if is_order else "posting_date"
        for d in frappe.get_all(
            doctype,
            filters={"name": ("in", list(doc_names))},
            fields=["name", f"{date_field} as date", "grand_total", "status"],
        ):
            docs[(doctype, d.name)] = frappe._dict(d, natures=set(), fulfilled_percent=None)

        done_field = {"Sales Order": "delivered_qty", "Purchase Order": "received_qty"}.get(doctype)
        item_fields = ["parent", "item_code", "qty", "amount"]
        if is_order:
            item_fields += [f"{done_field} as done_qty", "billed_amt"]

        weights = {}
        for item in frappe.get_all(
            f"{doctype} Item", filters={"parent": ("in", list(doc_names))}, fields=item_fields
        ):
            doc = docs.get((doctype, item.parent))
            if not doc:
                continue
            nature = get_item_nature(item.item_code)
            doc.natures.add(nature)
            if not is_order:
                continue

            amount = abs(flt(item.amount))
            if nature == SERVICES:
                done = min(flt(item.billed_amt), amount) / amount if amount else 0
            else:
                done = min(flt(item.done_qty), flt(item.qty)) / flt(item.qty) if flt(item.qty) else 0
            total, weighted = weights.get(item.parent, (0.0, 0.0))
            weights[item.parent] = (total + amount, weighted + amount * done)

        for name, (total, weighted) in weights.items():
            docs[(doctype, name)].fulfilled_percent = flt(weighted / total * 100, 2) if total else 0.0

    return docs


def get_item_nature(item_code):
    if not item_code:
        return GOODS
    is_fixed_asset, is_service = frappe.get_cached_value(
        "Item", item_code, ["is_fixed_asset", "custom_is_service_item"]
    ) or (0, 0)
    if is_fixed_asset:
        return ASSETS
    return SERVICES if is_service else GOODS


def build_advance(pe, refs, docs):
    # Allocation per linked document; an invoice that took over an order
    # advance also brings that order in (with nothing allocated to it).
    allocations = OrderedDict()
    for ref in refs:
        key = (ref.reference_doctype, ref.reference_name)
        allocations[key] = flt(allocations.get(key)) + flt(ref.allocated_amount)
        if ref.advance_voucher_type in ORDER_DOCTYPES and ref.advance_voucher_no:
            allocations.setdefault((ref.advance_voucher_type, ref.advance_voucher_no), 0.0)

    orders = [k for k in allocations if k[0] in ORDER_DOCTYPES]
    invoices = [k for k in allocations if k[0] not in ORDER_DOCTYPES]

    if not (orders or not refs or flt(pe.unallocated_amount) > 0):
        return None  # a plain invoice payment, not an advance

    order_allocated = sum(allocations[k] for k in orders)
    invoice_allocated = sum(allocations[k] for k in invoices)
    open_balance = flt(pe.unallocated_amount) + order_allocated

    if open_balance <= 0.005:
        allocation_status = "Fully Allocated"
    elif invoice_allocated > 0:
        allocation_status = "Partially Allocated"
    else:
        allocation_status = "Unallocated"

    natures = set()
    for key in allocations:
        natures |= docs.get(key, frappe._dict(natures=set())).natures

    order_totals = [(flt(docs[k].grand_total), docs[k].fulfilled_percent) for k in orders if k in docs]
    if order_totals:
        total = sum(t for t, _p in order_totals)
        fulfilled = flt(sum(t * flt(p) for t, p in order_totals) / total, 2) if total else 0.0
        delivery_status = get_delivery_status(fulfilled)
    else:
        fulfilled = None
        delivery_status = INVOICED if invoices else NOT_LINKED

    rate = flt(pe.exchange_rate) or 1
    row = frappe._dict(
        payment_entry=pe.name,
        posting_date=pe.posting_date,
        party_type=pe.party_type,
        party=pe.party,
        party_name=pe.party_name,
        currency=pe.currency,
        advance_amount=flt(pe.advance_amount),
        order_allocated=order_allocated,
        invoice_allocated=invoice_allocated,
        open_balance=open_balance,
        advance_for=get_nature(natures),
        fulfilled_percent=fulfilled,
        delivery_status=delivery_status,
        allocation_status=allocation_status,
        linked_to=LINKED_TO_ORDER if orders else LINKED_TO_INVOICE if invoices else LINKED_TO_NOTHING,
        # Not a column; drives the age chart and the oldest-advance card
        age=date_diff(today(), pe.posting_date),
    )

    return frappe._dict(
        row=row,
        natures=natures,
        base_open_balance=open_balance * rate,
        base_advance_amount=flt(pe.advance_amount) * rate,
    )


def get_nature(natures):
    """'Goods', 'Goods & Assets', 'Goods, Services & Assets', ... or 'Not Specified'."""
    found = [n for n in NATURES if n in natures]
    if not found:
        return NOT_SPECIFIED
    if len(found) == 1:
        return found[0]
    return ", ".join(found[:-1]) + " & " + found[-1]


def get_delivery_status(percent):
    if flt(percent) >= 99.99:
        return FULLY_DELIVERED
    return PARTLY_DELIVERED if flt(percent) > 0 else NOT_DELIVERED


def matches_filters(adv, filters):
    row = adv.row
    for fieldname in ("allocation_status", "linked_to", "delivery_status"):
        wanted = as_list(filters.get(fieldname))
        if wanted and row[fieldname] not in wanted:
            return False

    # An advance can cover several kinds; it matches if it has ANY chosen one
    wanted = set(as_list(filters.get("advance_for")))
    if wanted and not (adv.natures or {NOT_SPECIFIED}) & wanted:
        return False
    return True


# ---------------------------------------------------------------------------
# Chart and summary (company currency)
# ---------------------------------------------------------------------------

def get_age_bucket(age):
    for limit, label in AGE_BUCKETS:
        if limit is None or age <= limit:
            return label


def get_chart(advances, company_currency):
    open_advances = [a for a in advances if a.base_open_balance > 0]
    if not open_advances:
        return None

    labels = [label for _limit, label in AGE_BUCKETS]
    series = OrderedDict()
    for adv in sorted(open_advances, key=lambda a: (a.row.advance_for == NOT_SPECIFIED, a.row.advance_for)):
        values = series.setdefault(adv.row.advance_for, [0.0] * len(labels))
        values[labels.index(get_age_bucket(adv.row.age))] += adv.base_open_balance

    chart_colours = {GOODS: "#2490ef", SERVICES: "#7c5cfc", ASSETS: "#f59e0b", NOT_SPECIFIED: "#a1a1aa"}

    return {
        "title": _("Open advance balance by age (days)"),
        "data": {
            "labels": labels,
            "datasets": [{"name": _(n), "values": [flt(x, 2) for x in v]} for n, v in series.items()],
        },
        "type": "bar",
        "barOptions": {"stacked": 1},
        # Mixed kinds ("Goods & Assets", ...) share one colour
        "colors": [chart_colours.get(n, "#0f766e") for n in series],
        "fieldtype": "Currency",
        "options": company_currency,
    }


def get_report_summary(advances, company_currency):
    open_advances = [a for a in advances if a.base_open_balance > 0]

    def total(party_type=None, pending_only=False):
        return sum(
            a.base_open_balance
            for a in open_advances
            if (not party_type or a.row.party_type == party_type)
            and (not pending_only or a.row.delivery_status in PENDING_DELIVERY)
        )

    def card(label, value, indicator, datatype="Currency"):
        return {"label": _(label), "value": value, "indicator": indicator,
                "datatype": datatype, "currency": company_currency}

    return [
        card("Open Advances", len(open_advances), "Blue", "Int"),
        card("Paid To Suppliers, Still Open", total("Supplier"), "Orange"),
        card("Received From Customers, Still Open", total("Customer"), "Blue"),
        card("Linked To Orders Not Yet Delivered", total(pending_only=True), "Red"),
        card("Oldest Open Advance (Days)", max((a.row.age for a in open_advances), default=0), "Grey", "Int"),
    ]

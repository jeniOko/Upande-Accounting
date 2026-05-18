# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe

"""

Tracks how advance payments (both Customer and Supplier) have been
allocated/adjusted against invoices, and shows any remaining unallocated balance.

Tables used:
  `tabPayment Entry`           (pe)  — the advance payment header
  `tabPayment Entry Reference` (per) — each invoice allocation line
  `tabSales Invoice`           (si)  — customer invoice details
  `tabPurchase Invoice`        (pi)  — supplier invoice details


A Payment Entry is treated as an ADVANCE PAYMENT if ANY of the following is true:

  1. ORDER-BASED ADVANCE — at least one reference line points to a Sales Order
     or Purchase Order (payment made before invoicing).

  2. PURE UNALLOCATED ADVANCE — the payment has NO reference lines at all
     (money received/paid with nothing linked yet).

  3. PARTIALLY USED ADVANCE — the payment has reference lines (invoices or orders)
     but still carries unallocated_amount > 0, meaning part of the payment is
     still floating and not yet applied to any document.

Non-advance payments (fully allocated to invoices only, with zero unallocated
balance) are intentionally excluded.
"""

import frappe
from frappe import _
from frappe.utils import flt


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
            "label": _("Advance Payment"),
            "fieldname": "payment_entry",
            "fieldtype": "Link",
            "options": "Payment Entry",
            "width": 160,
        },
        {
            "label": _("Payment Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width": 110,
        },
        {
            "label": _("Party Type"),
            "fieldname": "party_type",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Party"),
            "fieldname": "party",
            "fieldtype": "Dynamic Link",
            "options": "party_type",
            "width": 160,
        },
        {
            "label": _("Party Name"),
            "fieldname": "party_name",
            "fieldtype": "Data",
            "width": 160,
        },
        {
            "label": _("Currency"),
            "fieldname": "currency",
            "fieldtype": "Link",
            "options": "Currency",
            "width": 80,
        },
        {
            "label": _("Advance Amount"),
            "fieldname": "paid_amount",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 140,
        },
        {
            "label": _("Invoice Type"),
            "fieldname": "reference_doctype",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("Invoice No"),
            "fieldname": "reference_name",
            "fieldtype": "Dynamic Link",
            "options": "reference_doctype",
            "width": 160,
        },
        {
            "label": _("Invoice Date"),
            "fieldname": "invoice_date",
            "fieldtype": "Date",
            "width": 110,
        },
        {
            "label": _("Invoice Amount"),
            "fieldname": "invoice_amount",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 140,
        },
        {
            "label": _("Allocated Amount"),
            "fieldname": "allocated_amount",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 140,
        },
        {
            "label": _("Total Allocated"),
            "fieldname": "total_allocated_amount",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 140,
        },
        {
            "label": _("Unallocated Balance"),
            "fieldname": "unallocated_amount",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 150,
        },
        {
            "label": _("Advance Type"),
            "fieldname": "advance_type",
            "fieldtype": "Data",
            "width": 170,
        },
        {
            "label": _("Payment Status"),
            "fieldname": "payment_status",
            "fieldtype": "Data",
            "width": 140,
        },
    ]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters):
    conditions, values = build_conditions(filters)

    # -----------------------------------------------------------------------
    # ADVANCE PAYMENT IDENTIFICATION
    # -----------------------------------------------------------------------
    # A Payment Entry qualifies as an advance if it meets ANY of:
    #
    #   Pattern 1 — ORDER-BASED ADVANCE
    #     EXISTS a reference line where reference_doctype IN
    #     ('Sales Order', 'Purchase Order')
    #
    #   Pattern 2 — PURE UNALLOCATED ADVANCE
    #     NOT EXISTS any reference line at all
    #
    #   Pattern 3 — PARTIALLY USED ADVANCE
    #     unallocated_amount > 0  (money still floating regardless of what
    #     references exist)
    #
    # The main SELECT LEFT JOINs only invoice reference lines so that
    # order-based or unallocated advances still produce a row (with NULLs
    # for invoice columns) rather than being silently dropped.
    # -----------------------------------------------------------------------

    sql = """
        SELECT
            pe.name                         AS payment_entry,
            pe.posting_date                 AS posting_date,
            pe.party_type                   AS party_type,
            pe.party                        AS party,
            pe.party_name                   AS party_name,

            -- Currency: "from" account for Receive (customer inflow),
            --           "to"   account for Pay    (supplier outflow)
            CASE
                WHEN pe.payment_type = 'Receive'
                    THEN pe.paid_from_account_currency
                ELSE pe.paid_to_account_currency
            END                             AS currency,

            pe.paid_amount                  AS paid_amount,
            pe.total_allocated_amount       AS total_allocated_amount,
            pe.unallocated_amount           AS unallocated_amount,

            -- Reference line columns (NULL when no invoice is linked yet)
            per.reference_doctype           AS reference_doctype,
            per.reference_name              AS reference_name,
            per.allocated_amount            AS allocated_amount,

            -- Invoice details resolved from SI or PI
            COALESCE(si.posting_date, pi.posting_date)  AS invoice_date,
            COALESCE(si.grand_total,  pi.grand_total)   AS invoice_amount,

            -- Human-readable advance type for quick triage
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` per2
                    WHERE per2.parent = pe.name
                      AND per2.reference_doctype IN ('Sales Order', 'Purchase Order')
                ) THEN 'Order-Based Advance'

                WHEN NOT EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` per3
                    WHERE per3.parent = pe.name
                ) THEN 'Unallocated Advance'

                ELSE 'Partially Used Advance'
            END                             AS advance_type,

            -- Allocation status
            CASE
                WHEN pe.unallocated_amount <= 0              THEN 'Fully Allocated'
                WHEN pe.total_allocated_amount > 0           THEN 'Partially Allocated'
                ELSE                                              'Unallocated'
            END                             AS payment_status

        FROM `tabPayment Entry` pe

        -- Only join invoice-type reference lines; order lines are handled
        -- via EXISTS subqueries above and do not need to produce extra rows.
        LEFT JOIN `tabPayment Entry Reference` per
            ON  per.parent           = pe.name
            AND per.reference_doctype IN ('Sales Invoice', 'Purchase Invoice')

        LEFT JOIN `tabSales Invoice` si
            ON  si.name              = per.reference_name
            AND per.reference_doctype = 'Sales Invoice'

        LEFT JOIN `tabPurchase Invoice` pi
            ON  pi.name              = per.reference_name
            AND per.reference_doctype = 'Purchase Invoice'

        WHERE
            pe.docstatus = 1
            AND pe.payment_type IN ('Receive', 'Pay')

            -- ── ADVANCE PAYMENT GATE ──────────────────────────────────────
            -- Include the Payment Entry only when at least one advance
            -- pattern is satisfied.
            AND (
                -- Pattern 1: linked to a Sales/Purchase Order
                EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` adv
                    WHERE adv.parent = pe.name
                      AND adv.reference_doctype IN ('Sales Order', 'Purchase Order')
                )
                OR
                -- Pattern 2: no references whatsoever
                NOT EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` adv2
                    WHERE adv2.parent = pe.name
                )
                OR
                -- Pattern 3: has references but money is still unallocated
                pe.unallocated_amount > 0
            )
            -- ─────────────────────────────────────────────────────────────

            {conditions}

        ORDER BY
            pe.posting_date DESC,
            pe.name,
            per.idx
    """.format(conditions=conditions)

    rows = frappe.db.sql(sql, values, as_dict=True)
    return rows


# ---------------------------------------------------------------------------
# Filter conditions builder
# ---------------------------------------------------------------------------

def build_conditions(filters):
    conditions = []
    values     = {}

    if filters.get("company"):
        conditions.append("AND pe.company = %(company)s")
        values["company"] = filters["company"]

    if filters.get("party_type"):
        conditions.append("AND pe.party_type = %(party_type)s")
        values["party_type"] = filters["party_type"]

    if filters.get("party"):
        conditions.append("AND pe.party = %(party)s")
        values["party"] = filters["party"]

    if filters.get("from_date"):
        conditions.append("AND pe.posting_date >= %(from_date)s")
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions.append("AND pe.posting_date <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    if filters.get("payment_status"):
        status = filters["payment_status"]
        if status == "Fully Allocated":
            conditions.append("AND pe.unallocated_amount <= 0")
        elif status == "Partially Allocated":
            conditions.append(
                "AND pe.total_allocated_amount > 0 AND pe.unallocated_amount > 0"
            )
        elif status == "Unallocated":
            conditions.append("AND pe.total_allocated_amount = 0")

    if filters.get("advance_type"):
        adv = filters["advance_type"]
        if adv == "Order-Based Advance":
            conditions.append("""
                AND EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` f1
                    WHERE f1.parent = pe.name
                      AND f1.reference_doctype IN ('Sales Order', 'Purchase Order')
                )
            """)
        elif adv == "Unallocated Advance":
            conditions.append("""
                AND NOT EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` f2
                    WHERE f2.parent = pe.name
                )
            """)
        elif adv == "Partially Used Advance":
            conditions.append("""
                AND pe.unallocated_amount > 0
                AND EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` f3
                    WHERE f3.parent = pe.name
                )
                AND NOT EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` f4
                    WHERE f4.parent = pe.name
                      AND f4.reference_doctype IN ('Sales Order', 'Purchase Order')
                )
            """)

    return " ".join(conditions), values
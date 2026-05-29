# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe


"""

Lists all submitted Payment Entries (both incoming and outgoing) with their
invoice allocations and balances. Every submitted Receive and Pay entry appears here.

Tables used:
  `tabPayment Entry`           (pe)  — payment header
  `tabPayment Entry Reference` (per) — allocation lines (invoices / orders)
  `tabSales Invoice`           (si)  — customer invoice details
  `tabPurchase Invoice`        (pi)  — supplier invoice details
"""

import frappe
from frappe import _


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
            "label": _("Type"),
            "fieldname": "payment_type",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Mode of Payment"),
            "fieldname": "mode_of_payment",
            "fieldtype": "Link",
            "options": "Mode of Payment",
            "width": 140,
        },
        {
            "label": _("Ref Date"),
            "fieldname": "reference_date",
            "fieldtype": "Date",
            "width": 120,
        },
        {
            "label": _("Cheque / Ref No"),
            "fieldname": "reference_no",
            "fieldtype": "Data",
            "width": 180,
        },
        
        {
            "label": _("Party Type"),
            "fieldname": "party_type",
            "fieldtype": "Data",
            "width": 100,
        },
        # {
        #     "label": _("Party"),
        #     "fieldname": "party",
        #     "fieldtype": "Dynamic Link",
        #     "options": "party_type",
        #     "width": 160,
        # },
        {
            "label": _("Party Name"),
            "fieldname": "party_name",
            "fieldtype": "Data",
            "width": 280,
        },
         {
            "label": _("Paid Amount"),
            "fieldname": "paid_amount",
            "fieldtype": "Currency",
            "options": "currency",
            "width": 140,
        },
        {
            "label": _("Currency"),
            "fieldname": "currency",
            "fieldtype": "Link",
            "options": "Currency",
            "width": 60,
        },
        
        {
            "label": _("Payment Entry"),
            "fieldname": "payment_entry",
            "fieldtype": "Link",
            "options": "Payment Entry",
            "width": 190,
        },
        {
            "label": _("Payment Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width": 110,
        },
       
        {
            "label": _("Against Doc Type"),
            "fieldname": "reference_doctype",
            "fieldtype": "Data",
            "width": 130,
        },
        {
            "label": _("Against Doc"),
            "fieldname": "reference_name",
            "fieldtype": "Dynamic Link",
            "options": "reference_doctype",
            "width": 190,
        },
        {
            "label": _("Document Date"),
            "fieldname": "invoice_date",
            "fieldtype": "Date",
            "width": 120,
        },
        {
            "label": _("Document Amount"),
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
            "label": _("Remarks"),
            "fieldname": "remarks",
            "fieldtype": "Data",
            "width": 200,
        },
    ]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters):
    conditions, values = build_conditions(filters)

    sql = """
        SELECT
            pe.name                         AS payment_entry,
            pe.posting_date                 AS posting_date,
            pe.payment_type                 AS payment_type,
            pe.party_type                   AS party_type,
            pe.party                        AS party,
            pe.party_name                   AS party_name,

            -- Currency: "from" account for Receive, "to" account for Pay
            CASE
                WHEN pe.payment_type = 'Receive'
                    THEN pe.paid_from_account_currency
                ELSE pe.paid_to_account_currency
            END                             AS currency,

            pe.paid_amount                  AS paid_amount,
            pe.mode_of_payment              AS mode_of_payment,
            pe.reference_no                 AS reference_no,
            pe.reference_date               AS reference_date,
            pe.total_allocated_amount       AS total_allocated_amount,
            pe.unallocated_amount           AS unallocated_amount,
            pe.remarks                      AS remarks,

            per.reference_doctype           AS reference_doctype,
            per.reference_name              AS reference_name,
            per.allocated_amount            AS allocated_amount,

            COALESCE(si.posting_date, pi.posting_date)  AS invoice_date,
            COALESCE(si.grand_total,  pi.grand_total)   AS invoice_amount

        FROM `tabPayment Entry` pe

        LEFT JOIN `tabPayment Entry Reference` per
            ON  per.parent            = pe.name
            AND per.reference_doctype IN (
                    'Sales Invoice', 'Purchase Invoice',
                    'Sales Order',   'Purchase Order'
                )

        LEFT JOIN `tabSales Invoice` si
            ON  si.name               = per.reference_name
            AND per.reference_doctype = 'Sales Invoice'

        LEFT JOIN `tabPurchase Invoice` pi
            ON  pi.name               = per.reference_name
            AND per.reference_doctype = 'Purchase Invoice'

        WHERE
            pe.docstatus = 1
            AND pe.payment_type IN ('Receive', 'Pay')
            {conditions}

        ORDER BY
            pe.posting_date DESC,
            pe.name,
            per.idx
    """.format(conditions=conditions)

    return frappe.db.sql(sql, values, as_dict=True)


# ---------------------------------------------------------------------------
# Filter conditions builder
# ---------------------------------------------------------------------------

def build_conditions(filters):
    conditions = []
    values     = {}

    if filters.get("company"):
        conditions.append("AND pe.company = %(company)s")
        values["company"] = filters["company"]

    if filters.get("payment_type"):
        conditions.append("AND pe.payment_type = %(payment_type)s")
        values["payment_type"] = filters["payment_type"]

    if filters.get("party_type"):
        conditions.append("AND pe.party_type = %(party_type)s")
        values["party_type"] = filters["party_type"]

    if filters.get("party"):
        conditions.append("AND pe.party = %(party)s")
        values["party"] = filters["party"]

    if filters.get("mode_of_payment"):
        conditions.append("AND pe.mode_of_payment = %(mode_of_payment)s")
        values["mode_of_payment"] = filters["mode_of_payment"]

    if filters.get("from_date"):
        conditions.append("AND pe.posting_date >= %(from_date)s")
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions.append("AND pe.posting_date <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    return " ".join(conditions), values
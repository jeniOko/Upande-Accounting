# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

# import frappe

"""

Generates the KRA VAT3 purchase-side filing summary.

Columns (matching the VAT3 format):
  A  Type of Purchase      — "Local" if supplier country = Kenya, else "Import"
  B  PIN of Supplier       — supplier tax_id
  C  Name of Supplier      — supplier name
  D  Invoice Date          — Purchase Invoice bill_date
  E  CU Invoice Number     — custom_invoice_number (custom field on PI)
  F  Description of Goods  — items_description (custom field on PI) or
                             first item's item_name as fallback
  G  Customs Entry Number  — custom_entry_number (custom field on PI, relevant for imports)
  H  Taxable Value (Ksh)   — net_total_company_currency (base net amount)
  I  Amount of VAT         — sum of tax rows in Purchase Taxes and Charges
                             where the linked Account has tax_report_type = "VAT3"

Filtering logic for VAT accounts:
  - Looks up all Accounts where account_type = "Tax" AND tax_report_type = "VAT3"
  - If the optional `vat_account` filter is provided, restricts to that one account
  - Sums tax_amount_company_currency from `tabPurchase Taxes and Charges`
    for matching account_head values
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
            "label": _("Type of Purchase"),
            "fieldname": "type_of_purchase",
            "fieldtype": "Data",
            "width": 130,
        },
        {
            "label": _("PIN of Supplier"),
            "fieldname": "tax_id",
            "fieldtype": "Data",
            "width": 140,
        },
        {
            "label": _("Name of Supplier"),
            "fieldname": "supplier_name",
            "fieldtype": "Data",
            "width": 220,
        },
        {
            "label": _("Invoice Date"),
            "fieldname": "bill_date",
            "fieldtype": "Date",
            "width": 110,
        },
        {
            "label": _("CU Invoice Number"),
            "fieldname": "custom_invoice_number",
            "fieldtype": "Data",
            "width": 160,
        },
        {
            "label": _("Description of Goods"),
            "fieldname": "description_of_goods",
            "fieldtype": "Data",
            "width": 200,
        },
        {
            "label": _("Customs Entry Number"),
            "fieldname": "custom_entry_number",
            "fieldtype": "Data",
            "width": 160,
        },
        {
            "label": _("Taxable Value (Ksh)"),
            "fieldname": "net_total_company_currency",
            "fieldtype": "Currency",
            "width": 160,
        },
        {
            "label": _("Amount of VAT"),
            "fieldname": "vat_amount",
            "fieldtype": "Currency",
            "width": 140,
        },
    ]


# ---------------------------------------------------------------------------
# VAT account resolution
# ---------------------------------------------------------------------------

def get_vat3_accounts(filters):
    """
    Return a list of account_head values tagged as VAT3 report accounts.
    If the user filtered on a specific account, return only that one
    (after confirming it is indeed a VAT3 account).
    """
    base_filters = {
        "account_type":     "Tax",
        "is_tax_report_account": 1,
        "tax_report_type":  "VAT3",
    }

    if filters.get("company"):
        base_filters["company"] = filters["company"]

    if filters.get("vat_account"):
        base_filters["name"] = filters["vat_account"]

    accounts = frappe.get_all(
        "Account",
        filters=base_filters,
        pluck="name",
    )
    return accounts


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters):
    vat_accounts = get_vat3_accounts(filters)

    if not vat_accounts:
        frappe.msgprint(
            _(
                "No accounts are tagged as VAT3 report accounts. "
                "Please open the relevant Tax accounts and enable "
                "<b>Include in Tax Report</b> → <b>VAT3</b>."
            ),
            indicator="orange",
            title=_("No VAT3 Accounts Found"),
        )
        return []

    # Build a safe IN clause placeholder list
    vat_account_placeholders = ", ".join(["%s"] * len(vat_accounts))

    conditions, values = build_conditions(filters)

    sql = """
        SELECT
            pi.name                         AS purchase_invoice,

            -- A: Type of Purchase — Local vs Import based on supplier country
            CASE
                WHEN IFNULL(sup.country, '') = 'Kenya' THEN 'Local'
                ELSE 'Import'
            END                             AS type_of_purchase,

            -- B: PIN of Supplier (KRA PIN stored in tax_id on Supplier)
            sup.tax_id                      AS tax_id,

            -- C: Supplier Name
            pi.supplier_name                AS supplier_name,

            -- D: Invoice Date (bill_date = supplier's invoice date)
            pi.bill_date                    AS bill_date,

            -- E: CU Invoice Number (custom field on Purchase Invoice)
            pi.custom_invoice_number        AS custom_invoice_number,

            -- F: Description of Goods
            --    Pulls the first line item's item_name from the items child table.
            --    If you later add a header-level `items_description` custom field
            --    on Purchase Invoice, wrap this in a COALESCE with that field.
            (
                SELECT pii.item_name
                FROM   `tabPurchase Invoice Item` pii
                WHERE  pii.parent = pi.name
                ORDER BY pii.idx
                LIMIT  1
            )                               AS description_of_goods,

            -- G: Customs Entry Number (custom field, relevant for imports)
            pi.custom_entry_number          AS custom_entry_number,

            -- H: Taxable Value — net amount in company currency
            pi.net_total                    AS net_total_company_currency,

            -- I: VAT Amount — summed from Purchase Taxes and Charges for VAT3 accounts
            COALESCE((
                SELECT SUM(ptc.tax_amount_after_discount_amount)
                FROM   `tabPurchase Taxes and Charges` ptc
                WHERE  ptc.parent      = pi.name
                  AND  ptc.account_head IN ({vat_placeholders})
            ), 0)                           AS vat_amount

        FROM `tabPurchase Invoice` pi

        JOIN `tabSupplier` sup
            ON sup.name = pi.supplier

        WHERE
            pi.docstatus = 1
            {conditions}

        ORDER BY
            pi.bill_date DESC,
            pi.name

    """.format(
        vat_placeholders=vat_account_placeholders,
        conditions=conditions,
    )

    # Merge vat_accounts (for the subquery IN clause) + filter condition values.
    # Both are plain lists so concatenation gives the correct positional order:
    #   [vat_account_1, ..., vat_account_n, filter_val_1, ..., filter_val_m]
    query_values = tuple(vat_accounts + values)

    rows = frappe.db.sql(sql, query_values, as_dict=True)
    return rows


# ---------------------------------------------------------------------------
# Filter conditions builder
# ---------------------------------------------------------------------------

def build_conditions(filters):
    """
    Returns (conditions_string, values_list) using only %s positional
    placeholders so they can be safely merged with the VAT account IN-clause
    values into a single tuple passed to frappe.db.sql.
    """
    conditions = []
    values     = []   # ordered list — must match %s placeholders in order

    if filters.get("company"):
        conditions.append("AND pi.company = %s")
        values.append(filters["company"])

    if filters.get("from_date"):
        conditions.append("AND pi.bill_date >= %s")
        values.append(filters["from_date"])

    if filters.get("to_date"):
        conditions.append("AND pi.bill_date <= %s")
        values.append(filters["to_date"])

    if filters.get("supplier"):
        conditions.append("AND pi.supplier = %s")
        values.append(filters["supplier"])

    if filters.get("type_of_purchase"):
        if filters["type_of_purchase"] == "Local":
            conditions.append("AND IFNULL(sup.country, '') = 'Kenya'")
        elif filters["type_of_purchase"] == "Import":
            conditions.append("AND IFNULL(sup.country, '') != 'Kenya'")

    return " ".join(conditions), values
# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Debtors Aging — per-invoice aging report (mirrors Accounts Receivable).

Inherits the full ReceivablePayableReport logic from ERPNext and adds:

  1. include_draft  — appends unsubmitted Sales Invoices as individual rows,
     each placed in the correct aging bucket and flagged  is_draft = True
     so the JS can style them (orange italic).

  2. in_party_currency  — already handled by the base class; exposed here
     as an explicit filter so users can toggle it.

Navigation: a top-right button switches to Debtors Aging Summary,
carrying company / report_date / ageing_based_on / range / party /
in_party_currency / include_draft across.
"""

import frappe
from frappe import _, scrub
from frappe.utils import flt, getdate

from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport


def execute(filters=None):
    filters = frappe._dict(filters or {})
    # This is always a Customer-only receivable report
    filters.party_type = "Customer"
    args = {
        "account_type": "Receivable",
        "naming_by": ["Selling Settings", "cust_master_name"],
    }
    return DebtorsAgingReport(filters).run(args)


class DebtorsAgingReport(ReceivablePayableReport):
    # ------------------------------------------------------------------
    # Columns — custom order, widths, and stripped-down set
    # ------------------------------------------------------------------

    def add_column(self, label, fieldname=None, fieldtype="Currency", options=None, width=120):
        if not fieldname:
            fieldname = scrub(label)
        if fieldtype == "Currency":
            options = "currency"
        # Base class forces Date width to 90 — we respect the caller's width instead
        self.columns.append(
            dict(label=label, fieldname=fieldname, fieldtype=fieldtype, options=options, width=width)
        )

    def get_columns(self):
        self.columns = []
        self.add_column(_("Posting Date"), fieldname="posting_date", fieldtype="Date",         width=120)
        self.add_column(_("Party Type"),   fieldname="party_type",   fieldtype="Data",         width=100)
        self.add_column(_("Party"),        fieldname="party",        fieldtype="Dynamic Link",
                        options="party_type", width=250)
        self.add_column(_("Voucher Type"), fieldname="voucher_type", fieldtype="Data",         width=130)
        self.add_column(_("Voucher No"),   fieldname="voucher_no",   fieldtype="Dynamic Link",
                        options="voucher_type", width=180)
        self.add_column(_("Due Date"), fieldname="due_date", fieldtype="Date",  width=120)
        self.add_column(_("Status"),   fieldname="status",   fieldtype="Data",  width=110)
        self.add_column(_("Currency"), fieldname="currency", fieldtype="Link",
                        options="Currency", width=80)

        if self.filters.based_on_payment_terms:
            self.add_column(_("Payment Term"),        fieldname="payment_term",        fieldtype="Data", width=150)
            self.add_column(_("Invoice Grand Total"), fieldname="invoice_grand_total",                   width=150)

        self.add_column(_("Paid Amount"),        fieldname="paid",        width=130)
        self.add_column(_("Invoiced Amount"),    fieldname="invoiced",    width=130)
        self.add_column(_("Credit Note"),        fieldname="credit_note", width=130)
        self.add_column(_("Outstanding Amount"), fieldname="outstanding", width=150)
        self.add_column(_("Age (Days)"),         fieldname="age",         fieldtype="Int", width=80)
        self.setup_ageing_columns()

        self.add_column(_("Customer LPO"),   fieldname="po_no",           fieldtype="Data", width=120)
        self.add_column(_("Territory"),      fieldname="territory",       fieldtype="Link",
                        options="Territory", width=120)
        self.add_column(_("Customer Group"), fieldname="customer_group",  fieldtype="Link",
                        options="Customer Group", width=130)

        if self.filters.show_sales_person:
            self.add_column(_("Sales Person"), fieldname="sales_person", fieldtype="Data", width=150)

        if self.filters.show_remarks:
            self.add_column(_("Remarks"), fieldname="remarks", fieldtype="Text", width=200)

        if self.filters.show_future_payments:
            self.add_column(_("Future Payment Ref"),    fieldname="future_ref",        fieldtype="Data", width=150)
            self.add_column(_("Future Payment Amount"), fieldname="future_amount",                       width=150)
            self.add_column(_("Remaining Balance"),     fieldname="remaining_balance",                   width=150)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _set_row_status(self, row):
        """Populate row.status based on due_date vs report_date.

        Due        — due_date on or before report_date (overdue)
        Almost Due — due_date is 1-7 days after report_date
        Not Due    — due_date is more than 7 days away
        """
        due_date = row.get("due_date")
        if not due_date:
            row.status = ""
            return

        days_until_due = (getdate(due_date) - getdate(self.filters.report_date)).days

        if days_until_due > 7:
            row.status = "Not Due"
        elif days_until_due > 0:
            row.status = "Almost Due"
        else:
            row.status = "Overdue"

    def append_row(self, row):
        super().append_row(row)
        self._set_row_status(row)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def get_data(self):
        super().get_data()
        if self.filters.get("include_draft"):
            self._append_draft_rows()

    def _append_draft_rows(self):
        company      = self.filters.company
        report_date  = getdate(self.filters.report_date)
        in_party_cur = self.filters.get("in_party_currency")

        sql_filters = {
            "docstatus":    0,
            "company":      company,
            "posting_date": ("<=", report_date),
        }
        if self.filters.get("party"):
            sql_filters["customer"] = ("in", self.filters.get("party"))

        draft_sis = frappe.get_all(
            "Sales Invoice",
            filters=sql_filters,
            fields=[
                "name", "customer", "posting_date", "due_date",
                "currency", "grand_total", "base_grand_total", "po_no",
            ],
        )

        for si in draft_sis:
            amount = flt(si.grand_total) if in_party_cur else flt(si.base_grand_total)
            cust_currency = (
                frappe.db.get_value("Customer", si.customer, "default_currency")
                or self.company_currency
            )

            row = frappe._dict(
                posting_date        = si.posting_date,
                party_type          = "Customer",
                party               = si.customer,
                party_account       = "",
                voucher_type        = "Sales Invoice",
                voucher_no          = si.name,
                due_date            = si.due_date,
                invoiced            = amount,
                invoice_grand_total = amount,
                paid                = 0.0,
                credit_note         = 0.0,
                outstanding         = amount,
                account_currency    = cust_currency,
                currency            = cust_currency if in_party_cur else self.company_currency,
                po_no               = si.get("po_no") or "",
                is_draft            = True,
            )

            # Enrich with customer_name, territory, customer_group etc.
            party_details = self.get_party_details(si.customer) or {}
            row.update(party_details)

            # Compute age and place in the correct range bucket
            self.set_ageing(row)
            self._set_row_status(row)

            self.data.append(row)

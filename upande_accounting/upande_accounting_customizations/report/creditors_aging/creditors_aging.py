# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Creditors Aging — per-invoice aging report (mirrors Accounts Payable).

Inherits the full ReceivablePayableReport logic from ERPNext and adds:

  1. Custom column layout  — Bill No and Bill Date as primary identifiers;
     Voucher No moved to the last column.

  2. Status column  — Overdue / Almost Due / Not Due computed from due_date
     vs the As On Date (report_date).

  3. include_draft  — appends unsubmitted Purchase Invoices as individual rows
     (orange italic), each placed in the correct aging bucket.

  4. in_party_currency  — exposed as an explicit filter; handled by the base.

Navigation: "Creditors Aging Summary" button switches to the per-supplier
summary carrying all filters across.
"""

import frappe
from frappe import _, scrub
from frappe.utils import flt, getdate

from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport


def execute(filters=None):
    filters = frappe._dict(filters or {})
    filters.party_type = "Supplier"
    args = {
        "account_type": "Payable",
        "naming_by": ["Buying Settings", "supp_master_name"],
    }
    return CreditorsAgingReport(filters).run(args)


class CreditorsAgingReport(ReceivablePayableReport):
    # ------------------------------------------------------------------
    # Columns
    # ------------------------------------------------------------------

    def add_column(self, label, fieldname=None, fieldtype="Currency", options=None, width=120):
        if not fieldname:
            fieldname = scrub(label)
        if fieldtype == "Currency":
            options = "currency"
        self.columns.append(
            dict(label=label, fieldname=fieldname, fieldtype=fieldtype, options=options, width=width)
        )

    def get_columns(self):
        self.columns = []
        self.add_column(_("Posting Date"), fieldname="posting_date", fieldtype="Date",        width=120)
        self.add_column(_("Party Type"),   fieldname="party_type",   fieldtype="Data",        width=100)
        self.add_column(_("Party"),        fieldname="party",        fieldtype="Dynamic Link",
                        options="party_type", width=250)
        self.add_column(_("Voucher Type"), fieldname="voucher_type", fieldtype="Data",        width=130)
        self.add_column(_("Bill No"),      fieldname="bill_no",      fieldtype="Data",        width=150)
        self.add_column(_("Bill Date"),    fieldname="bill_date",    fieldtype="Date",        width=120)
        self.add_column(_("Due Date"),     fieldname="due_date",     fieldtype="Date",        width=120)
        self.add_column(_("Status"),       fieldname="status",       fieldtype="Data",        width=110)
        self.add_column(_("Currency"),     fieldname="currency",     fieldtype="Link",
                        options="Currency", width=80)

        if self.filters.based_on_payment_terms:
            self.add_column(_("Payment Term"),        fieldname="payment_term",        fieldtype="Data", width=150)
            self.add_column(_("Invoice Grand Total"), fieldname="invoice_grand_total",                   width=150)

        self.add_column(_("Paid Amount"),        fieldname="paid",        width=130)
        self.add_column(_("Invoiced Amount"),    fieldname="invoiced",    width=130)
        self.add_column(_("Debit Note"),         fieldname="credit_note", width=130)
        self.add_column(_("Outstanding Amount"), fieldname="outstanding", width=150)
        self.add_column(_("Age (Days)"),         fieldname="age",         fieldtype="Int", width=80)
        self.setup_ageing_columns()

        self.add_column(_("Supplier Group"), fieldname="supplier_group", fieldtype="Link",
                        options="Supplier Group", width=130)

        if self.filters.show_remarks:
            self.add_column(_("Remarks"), fieldname="remarks", fieldtype="Text", width=200)

        if self.filters.show_future_payments:
            self.add_column(_("Future Payment Ref"),    fieldname="future_ref",        fieldtype="Data", width=150)
            self.add_column(_("Future Payment Amount"), fieldname="future_amount",                       width=150)
            self.add_column(_("Remaining Balance"),     fieldname="remaining_balance",                   width=150)

        # Voucher No last
        self.add_column(_("Voucher No"), fieldname="voucher_no", fieldtype="Dynamic Link",
                        options="voucher_type", width=180)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _set_row_status(self, row):
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
            sql_filters["supplier"] = ("in", self.filters.get("party"))

        draft_pis = frappe.get_all(
            "Purchase Invoice",
            filters=sql_filters,
            fields=[
                "name", "supplier", "posting_date", "due_date",
                "bill_no", "bill_date",
                "currency", "grand_total", "base_grand_total",
            ],
        )

        for pi in draft_pis:
            amount = flt(pi.grand_total) if in_party_cur else flt(pi.base_grand_total)
            supp_currency = (
                frappe.db.get_value("Supplier", pi.supplier, "default_currency")
                or self.company_currency
            )

            row = frappe._dict(
                posting_date        = pi.posting_date,
                party_type          = "Supplier",
                party               = pi.supplier,
                party_account       = "",
                voucher_type        = "Purchase Invoice",
                voucher_no          = pi.name,
                due_date            = pi.due_date,
                bill_no             = pi.bill_no or "",
                bill_date           = pi.bill_date,
                invoiced            = amount,
                invoice_grand_total = amount,
                paid                = 0.0,
                credit_note         = 0.0,
                outstanding         = amount,
                account_currency    = supp_currency,
                currency            = supp_currency if in_party_cur else self.company_currency,
                is_draft            = True,
            )

            party_details = self.get_party_details(pi.supplier) or {}
            row.update(party_details)
            self.set_ageing(row)
            self._set_row_status(row)

            self.data.append(row)

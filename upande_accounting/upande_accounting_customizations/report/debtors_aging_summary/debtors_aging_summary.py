# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Debtors Aging Summary — summary aging report (one row per customer).

Builds on ERPNext's AccountsReceivableSummary (which reads from the
Payment Ledger Entry) and layers in two additions:

  1. include_draft  — fold unsubmitted Sales Invoices into the totals.
     Draft amounts are placed in the correct aging bucket and flagged
     with  has_draft = True  so the JS can style them.

  2. in_party_currency  — amounts shown in the customer's own currency
     (passes through to the base ReceivablePayableReport which already
     handles this; it is simply not exposed in the standard AR Summary).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import (
    AccountsReceivableSummary,
)
from erpnext.accounts.utils import get_party_types_from_account_type


def execute(filters=None):
    args = {
        "account_type": "Receivable",
        "naming_by": ["Selling Settings", "cust_master_name"],
    }
    return DebtorsAgingReport(filters).run(args)


class DebtorsAgingReport(AccountsReceivableSummary):
    def run(self, args):
        self.account_type = args.get("account_type")
        self.party_type = get_party_types_from_account_type(self.account_type)
        self.party_naming_by = frappe.db.get_value(
            args.get("naming_by")[0], None, args.get("naming_by")[1]
        )
        self.get_columns()
        self.get_data(args)
        return self.columns, self.data

    # ------------------------------------------------------------------
    # Columns — Currency positioned after Party
    # ------------------------------------------------------------------

    def get_columns(self):
        self.columns = []
        self.add_column(_("Party Type"), fieldname="party_type", fieldtype="Data",        width=100)
        self.add_column(_("Party"),      fieldname="party",      fieldtype="Dynamic Link",
                        options="party_type", width=300)
        self.add_column(_("Currency"),   fieldname="currency",   fieldtype="Link",
                        options="Currency", width=80)

        if self.party_naming_by == "Naming Series":
            self.add_column(_("Customer Name"), fieldname="party_name", fieldtype="Data", width=200)

        self.add_column(_("Advance Amount"),     fieldname="advance",      width=130)
        self.add_column(_("Invoiced Amount"),    fieldname="invoiced",     width=130)
        self.add_column(_("Paid Amount"),        fieldname="paid",         width=130)
        self.add_column(_("Credit Note"),        fieldname="credit_note",  width=130)
        self.add_column(_("Outstanding Amount"), fieldname="outstanding",  width=150)

        self.setup_ageing_columns()
        self.add_column(_("Total Amount Due"), fieldname="total_due", width=150)

        self.add_column(_("Territory"),      fieldname="territory",      fieldtype="Link",
                        options="Territory", width=120)
        self.add_column(_("Customer Group"), fieldname="customer_group", fieldtype="Link",
                        options="Customer Group", width=130)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def get_data(self, args):
        super().get_data(args)
        if self.filters.get("include_draft"):
            self._merge_draft_invoices()

    def _merge_draft_invoices(self):
        company       = self.filters.company
        report_date   = getdate(self.filters.report_date)
        age_based_on  = self.filters.get("ageing_based_on") or "Due Date"
        in_party_cur  = self.filters.get("in_party_currency")
        ranges        = self.ranges         # ["30", "60", "90", "120"]
        range_numbers = self.range_numbers  # [1, 2, 3, 4, 5]

        draft_sis = frappe.db.sql(
            """
            SELECT
                customer,
                name,
                posting_date,
                due_date,
                currency,
                grand_total,
                base_grand_total
            FROM `tabSales Invoice`
            WHERE docstatus      = 0
              AND company        = %s
              AND posting_date  <= %s
            """,
            (company, report_date),
            as_dict=True,
        )

        # Respect party filter
        if self.filters.get("party"):
            allowed   = set(self.filters.get("party"))
            draft_sis = [d for d in draft_sis if d.customer in allowed]

        if not draft_sis:
            return

        # Aggregate per customer into range buckets
        draft_by_party = {}
        for si in draft_sis:
            party = si.customer
            if party not in draft_by_party:
                draft_by_party[party] = frappe._dict(
                    outstanding=0.0,
                    invoiced=0.0,
                    currency=si.currency,
                    **{f"range{rn}": 0.0 for rn in range_numbers},
                )

            amount = flt(si.grand_total) if in_party_cur else flt(si.base_grand_total)

            entry_date = (
                (si.due_date or si.posting_date)
                if age_based_on == "Due Date"
                else si.posting_date
            )
            age = (report_date - getdate(entry_date)).days if entry_date else 0

            # Place in the first bucket whose cap >= age; last bucket is the overflow
            bucket = len(ranges)
            for i, r in enumerate(ranges):
                if age <= cint(r):
                    bucket = i
                    break

            draft_by_party[party].outstanding            += amount
            draft_by_party[party].invoiced               += amount
            draft_by_party[party][f"range{bucket + 1}"] += amount

        # Merge into existing summary rows or add new ones
        existing = {row.party: row for row in self.data}

        for party, draft in draft_by_party.items():
            if party in existing:
                row = existing[party]
                row.outstanding = flt(row.outstanding) + draft.outstanding
                row.invoiced    = flt(row.invoiced)    + draft.invoiced
                for rn in range_numbers:
                    row[f"range{rn}"] = flt(row.get(f"range{rn}", 0)) + draft[f"range{rn}"]
                row.total_due = sum(flt(row.get(f"range{rn}", 0)) for rn in range_numbers)
                row.has_draft = True
            else:
                # Customer has only draft invoices — no Payment Ledger history
                party_name = frappe.db.get_value("Customer", party, "customer_name")
                new_row = frappe._dict(
                    party       = party,
                    party_type  = "Customer",
                    party_name  = party_name or party,
                    outstanding = draft.outstanding,
                    invoiced    = draft.invoiced,
                    paid        = 0.0,
                    credit_note = 0.0,
                    advance     = 0.0,
                    currency    = draft.currency,
                    has_draft   = True,
                    total_due   = sum(flt(draft.get(f"range{rn}", 0)) for rn in range_numbers),
                )
                for rn in range_numbers:
                    new_row[f"range{rn}"] = draft[f"range{rn}"]
                self.data.append(new_row)

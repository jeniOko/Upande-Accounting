# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Creditors Aging Summary — one row per supplier (mirrors Accounts Payable Summary).

Builds on ERPNext's AccountsReceivableSummary (which handles both payable and
receivable) and adds:

  1. include_draft  — fold unsubmitted Purchase Invoices into the totals.
     Amounts are placed in the correct aging bucket; rows flagged has_draft = True
     receive a "Has Drafts" badge in the JS.

  2. in_party_currency  — exposed as an explicit filter. The base report puts
     invoiced / paid / outstanding in party currency, but still takes Advance
     Amount in company currency; it is re-derived from the Payment Ledger in
     the supplier's account currency (aging_utils), covering Payment Entries
     and Journal Entries alike.

  3. Custom column layout  — Currency after Party, Debit Note instead of Credit Note,
     Supplier Group instead of Territory / Customer Group. Debit Note /
     Outstanding / Invoiced Amount sit after the ageing buckets, just before
     Supplier Group; the base report's "<0" (not-yet-due) ageing column is
     dropped.

  4. show_advance_payment  — off by default, which hides the Advance Amount
     and Paid Amount columns so the summary reads as pure invoice totals.
     Checking it shows both (the underlying figures are always computed;
     this only gates what's rendered).

  5. group_by  — Group by Currency / Supplier Group: rows sit under a bold
     header per group with a sub-total per currency (aging_utils.group_rows).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from erpnext.accounts.report.accounts_receivable_summary.accounts_receivable_summary import (
    AccountsReceivableSummary,
)
from erpnext.accounts.utils import get_party_types_from_account_type

from upande_accounting.upande_accounting_customizations.report.aging_utils import (
    add_party_name_column,
    group_rows,
    set_advance_in_party_currency,
    sort_by_party,
)


def execute(filters=None):
    args = {
        "account_type": "Payable",
        "naming_by": ["Buying Settings", "supp_master_name"],
    }
    return CreditorsAgingSummaryReport(filters).run(args)


class CreditorsAgingSummaryReport(AccountsReceivableSummary):
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
    # Columns — Currency after Party, Supplier Group at the end
    # ------------------------------------------------------------------

    def get_columns(self):
        self.columns = []
        self.add_column(_("Party Type"), fieldname="party_type", fieldtype="Data",        width=100)
        self.add_column(_("Party"),      fieldname="party",      fieldtype="Dynamic Link",
                        options="party_type", width=250)
        self.add_column(_("Currency"),   fieldname="currency",   fieldtype="Link",
                        options="Currency", width=80)

        if self.filters.get("show_advance_payment"):
            self.add_column(_("Advance Amount"), fieldname="advance", width=130)

        self.setup_ageing_columns()
        self.add_column(_("Total Amount Due"), fieldname="total_due", width=150)

        # Debit Note / Outstanding / Invoiced / Paid sit after the ageing
        # buckets, right before Supplier Group. Paid Amount only makes sense
        # once advance payments are in view, so it's gated on
        # show_advance_payment, same as Advance Amount above.
        self.add_column(_("Debit Note"),         fieldname="credit_note",  width=130)
        self.add_column(_("Outstanding Amount"), fieldname="outstanding",  width=150)
        self.add_column(_("Invoiced Amount"),    fieldname="invoiced",     width=130)
        if self.filters.get("show_advance_payment"):
            self.add_column(_("Paid Amount"), fieldname="paid", width=130)

        self.add_column(_("Supplier Group"), fieldname="supplier_group", fieldtype="Link",
                        options="Supplier Group", width=130)

    def setup_ageing_columns(self):
        """Same buckets as the base report, minus the "<0" (not-yet-due)
        column — with ageing now defaulting to Posting Date, a row's
        relevant date is essentially never after the As On Date, so that
        bucket is just noise.
        """
        self.ageing_column_labels = [_("<0")]
        ranges = [*self.ranges, _("Above")]

        prev_range_value = 0
        for idx, curr_range_value in enumerate(ranges):
            label = f"{prev_range_value}-{curr_range_value}"
            self.add_column(label=label, fieldname="range" + str(idx + 1))
            self.ageing_column_labels.append(label)
            if curr_range_value.isdigit():
                prev_range_value = cint(curr_range_value) + 1

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def get_data(self, args):
        super().get_data(args)

        if self.filters.get("in_party_currency"):
            set_advance_in_party_currency(self.data, self.party_type, self.filters)

        if self.filters.get("include_draft"):
            self._merge_draft_invoices()

        sort_by_party(self.data, "Supplier")
        add_party_name_column(self.columns, self.data, "Supplier")
        self.data = group_rows(self.data, self.columns, self.filters)

    # ------------------------------------------------------------------
    # Draft Purchase Invoices
    # ------------------------------------------------------------------

    def _merge_draft_invoices(self):
        company       = self.filters.company
        report_date   = getdate(self.filters.report_date)
        age_based_on  = self.filters.get("ageing_based_on") or "Due Date"
        in_party_cur  = self.filters.get("in_party_currency")
        ranges        = self.ranges
        range_numbers = self.range_numbers

        draft_pis = frappe.db.sql(
            """
            SELECT
                supplier,
                name,
                posting_date,
                due_date,
                currency,
                grand_total,
                base_grand_total
            FROM `tabPurchase Invoice`
            WHERE docstatus      = 0
              AND company        = %s
              AND posting_date  <= %s
            """,
            (company, report_date),
            as_dict=True,
        )

        if self.filters.get("party"):
            allowed   = set(self.filters.get("party"))
            draft_pis = [d for d in draft_pis if d.supplier in allowed]

        if not draft_pis:
            return

        draft_by_party = {}
        for pi in draft_pis:
            party = pi.supplier
            if party not in draft_by_party:
                draft_by_party[party] = frappe._dict(
                    outstanding=0.0,
                    invoiced=0.0,
                    currency=pi.currency,
                    **{f"range{rn}": 0.0 for rn in range_numbers},
                )

            amount = flt(pi.grand_total) if in_party_cur else flt(pi.base_grand_total)

            entry_date = (
                (pi.due_date or pi.posting_date)
                if age_based_on == "Due Date"
                else pi.posting_date
            )
            age = (report_date - getdate(entry_date)).days if entry_date else 0

            bucket = len(ranges)
            for i, r in enumerate(ranges):
                if age <= cint(r):
                    bucket = i
                    break

            draft_by_party[party].outstanding            += amount
            draft_by_party[party].invoiced               += amount
            draft_by_party[party][f"range{bucket + 1}"] += amount

        company_currency = frappe.get_cached_value("Company", company, "default_currency")
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
                details = frappe.db.get_value(
                    "Supplier", party, ["supplier_name", "supplier_group"], as_dict=True
                ) or {}
                new_row = frappe._dict(
                    party       = party,
                    party_type  = "Supplier",
                    party_name  = details.get("supplier_name") or party,
                    supplier_group = details.get("supplier_group"),
                    outstanding = draft.outstanding,
                    invoiced    = draft.invoiced,
                    paid        = 0.0,
                    credit_note = 0.0,
                    advance     = 0.0,
                    currency    = draft.currency if in_party_cur else company_currency,
                    has_draft   = True,
                    total_due   = sum(flt(draft.get(f"range{rn}", 0)) for rn in range_numbers),
                )
                for rn in range_numbers:
                    new_row[f"range{rn}"] = draft[f"range{rn}"]
                self.data.append(new_row)

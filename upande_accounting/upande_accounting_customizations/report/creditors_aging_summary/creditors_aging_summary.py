# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""
Creditors Aging Summary — one row per supplier (mirrors Accounts Payable Summary).

Builds on ERPNext's AccountsReceivableSummary (which handles both payable and
receivable) and adds:

  1. include_draft  — fold unsubmitted Purchase Invoices into the totals.
     Amounts are placed in the correct aging bucket; rows flagged has_draft = True
     receive a "Has Drafts" badge in the JS.

  2. in_party_currency  — exposed as an explicit filter. The base report converts
     invoice-derived figures (invoiced) to party currency, but NOT the
     payment-derived figures (paid, advance) in the summary rollup. This report
     re-derives paid and advance directly from Payment Entries in party currency
     (each payment at its own exchange rate), then recomputes outstanding.

  3. Custom column layout  — Currency after Party, Debit Note instead of Credit Note,
     Supplier Group instead of Territory / Customer Group.
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

        if self.party_naming_by == "Naming Series":
            self.add_column(_("Supplier Name"), fieldname="party_name", fieldtype="Data", width=200)

        self.add_column(_("Advance Amount"),     fieldname="advance",      width=130)
        self.add_column(_("Invoiced Amount"),    fieldname="invoiced",     width=130)
        self.add_column(_("Paid Amount"),        fieldname="paid",         width=130)
        self.add_column(_("Debit Note"),         fieldname="credit_note",  width=130)
        self.add_column(_("Outstanding Amount"), fieldname="outstanding",  width=150)

        self.setup_ageing_columns()
        self.add_column(_("Total Amount Due"), fieldname="total_due", width=150)

        self.add_column(_("Supplier Group"), fieldname="supplier_group", fieldtype="Link",
                        options="Supplier Group", width=130)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def get_data(self, args):
        super().get_data(args)

        if self.filters.get("in_party_currency"):
            self._fix_party_currency_payments()

        if self.filters.get("include_draft"):
            self._merge_draft_invoices()

    # ------------------------------------------------------------------
    # Paid / Advance in party currency (each payment at its own rate)
    # ------------------------------------------------------------------

    def _fix_party_currency_payments(self):
        """
        The base summary report leaves `paid` and `advance` in company currency.
        Re-derive them per supplier directly from submitted Payment Entries in
        the party's own currency, then recompute outstanding.

        - paid    = payment amount allocated against Purchase Invoices (party ccy)
        - advance = payment amount NOT allocated to any invoice (party ccy)

        Each Payment Entry contributes at its own exchange rate, because we read
        the party-currency field (paid_amount / received_amount) stored on the PE.
        """
        company     = self.filters.company
        report_date = getdate(self.filters.report_date)

        party_filter = ""
        params = [company, report_date]
        if self.filters.get("party"):
            parties = self.filters.get("party")
            if isinstance(parties, str):
                parties = [parties]
            placeholders = ", ".join(["%s"] * len(parties))
            party_filter = f" AND pe.party IN ({placeholders})"
            params.extend(parties)

        # For a "Pay" Payment Entry to a supplier, the party-currency amount is
        # `paid_amount` (the amount leaving in the party/transaction currency is
        # `paid_amount` when paid_from is company ccy; ERPNext stores the party
        # side in `paid_amount` for payments where party currency == paid_to ccy).
        # We use `paid_amount` as the party-currency figure and fall back to
        # base_paid_amount only if paid_amount is zero.
        payments = frappe.db.sql(
            f"""
            SELECT
                pe.name              AS payment_entry,
                pe.party             AS party,
                pe.paid_amount       AS paid_amount,
                pe.base_paid_amount  AS base_paid_amount,
                pe.unallocated_amount AS unallocated_amount,
                pe.source_exchange_rate AS source_rate,
                pe.target_exchange_rate AS target_rate
            FROM `tabPayment Entry` pe
            WHERE pe.docstatus    = 1
              AND pe.payment_type = 'Pay'
              AND pe.party_type   = 'Supplier'
              AND pe.company      = %s
              AND pe.posting_date <= %s
              {party_filter}
            """,
            params,
            as_dict=True,
        )

        if not payments:
            return

        pe_names = [p.payment_entry for p in payments]

        # Allocated amounts per Payment Entry against Purchase Invoices.
        # `allocated_amount` on the reference row is in the PE's paid-from
        # currency context; we use the ratio of allocated/total to split the
        # party-currency paid_amount into paid vs advance, so currency stays
        # consistent regardless of which field ERPNext populated.
        placeholders = ", ".join(["%s"] * len(pe_names))
        refs = frappe.db.sql(
            f"""
            SELECT
                per.parent           AS payment_entry,
                per.reference_doctype AS reference_doctype,
                per.allocated_amount  AS allocated_amount
            FROM `tabPayment Entry Reference` per
            WHERE per.parent IN ({placeholders})
              AND per.docstatus = 1
            """,
            pe_names,
            as_dict=True,
        )

        allocated_by_pe = {}
        for r in refs:
            if r.reference_doctype == "Purchase Invoice":
                allocated_by_pe[r.payment_entry] = (
                    allocated_by_pe.get(r.payment_entry, 0.0) + flt(r.allocated_amount)
                )

        paid_by_party    = {}
        advance_by_party = {}

        for pe in payments:
            party = pe.party

            # Party-currency total for this payment.
            party_total = flt(pe.paid_amount) or flt(pe.base_paid_amount)
            if not party_total:
                continue

            # Split into allocated (paid) vs unallocated (advance) using the
            # company-currency proportions, then apply to the party-ccy total.
            base_total = flt(pe.base_paid_amount) or party_total
            allocated_base = allocated_by_pe.get(pe.payment_entry, 0.0)

            if base_total:
                alloc_ratio = min(max(allocated_base / base_total, 0.0), 1.0)
            else:
                alloc_ratio = 0.0

            paid_part    = party_total * alloc_ratio
            advance_part = party_total - paid_part

            paid_by_party[party]    = paid_by_party.get(party, 0.0) + paid_part
            advance_by_party[party] = advance_by_party.get(party, 0.0) + advance_part

        # Overwrite the company-currency paid/advance left by the base report,
        # then recompute outstanding for each affected row.
        for row in self.data:
            party = row.get("party")
            if party in paid_by_party or party in advance_by_party:
                row.paid    = flt(paid_by_party.get(party, 0.0))
                row.advance = flt(advance_by_party.get(party, 0.0))
                row.outstanding = (
                    flt(row.get("invoiced"))
                    - flt(row.get("paid"))
                    - flt(row.get("credit_note"))
                    + flt(row.get("advance"))
                )

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
                supp_name = frappe.db.get_value("Supplier", party, "supplier_name")
                new_row = frappe._dict(
                    party       = party,
                    party_type  = "Supplier",
                    party_name  = supp_name or party,
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

"""
Bank Clearance: let the Cheque / Reference No on each row be corrected to
what the bank statement shows, alongside the clearance date.

When Update Clearance Date is clicked, every row whose reference differs from
its voucher's is written back to that voucher (Payment Entry reference_no,
Journal Entry cheque_no), its remarks are rebuilt (they quote the reference
and are copied onto the ledger), and the voucher is reposted through ERPNext's
Repost Accounting Ledger so the GL / Payment Ledger remarks follow.

Reposting is never switched on here: if Payment Entry / Journal Entry are not
in Accounts Settings > Repost Allowed Types, nothing is changed and the user
is told to ask for it to be enabled.
"""

import frappe
from frappe import _
from frappe.utils import get_link_to_form

from erpnext.accounts.doctype.repost_accounting_ledger.repost_accounting_ledger import (
    get_allowed_types_from_settings,
)

# Voucher type -> its reference field. Paid Purchase Invoices (bill_no is the
# supplier's invoice number) and POS Sales Invoices also list here but are
# not bank references this tool should rewrite.
REFERENCE_FIELDS = {
    "Payment Entry": "reference_no",
    "Journal Entry": "cheque_no",
}


class BankClearanceMixin:
    """Mixed in ahead of ERPNext's BankClearance (hooks.extend_doctype_class)."""

    @frappe.whitelist()
    def get_payment_entries(self):
        super().get_payment_entries()
        # What the voucher held when loaded, so the form can tell which
        # references the user has changed.
        for row in self.payment_entries:
            row.custom_original_reference_no = row.cheque_number

    @frappe.whitelist()
    def update_clearance_date(self):
        changed = self.update_reference_numbers()
        if changed and not self.include_reconciled_entries and not any(
            row.clearance_date for row in self.payment_entries
        ):
            # Only references were corrected; ERPNext's own step would just
            # report "Clearance Date not mentioned". Reload the rows instead.
            self.get_payment_entries()
            return
        super().update_clearance_date()

    def update_reference_numbers(self):
        changes = self.get_reference_changes()
        if not changes:
            return False

        not_allowed = sorted({row.payment_document for row, _old in changes} - set(get_allowed_types_from_settings()))
        if not_allowed:
            frappe.throw(
                _(
                    "Reference numbers can't be changed because {0} can't be reposted on this site. "
                    "Ask an administrator to add {0} to Repost Allowed Types in {1}. Nothing has been changed."
                ).format(
                    frappe.bold(", ".join(_(dt) for dt in not_allowed)),
                    get_link_to_form("Accounts Settings", "Accounts Settings"),
                ),
                title=_("Reposting Not Enabled"),
            )

        updated = []
        for row, old in changes:
            new = row.cheque_number.strip()
            voucher = frappe.get_doc(row.payment_document, row.payment_entry)
            voucher.check_permission("write")
            if voucher.docstatus != 1:
                frappe.throw(
                    _("Row {0}: Only a submitted voucher's reference can be changed.").format(row.idx),
                    title=_("Invalid Voucher"),
                )

            fieldname = REFERENCE_FIELDS[voucher.doctype]
            voucher.set(fieldname, new)
            # Rebuild the remarks that quote the reference; each skips itself
            # when the user wrote custom remarks.
            if voucher.doctype == "Payment Entry":
                voucher.set_remarks()
                voucher.db_set({fieldname: new, "remarks": voucher.remarks})
            else:
                voucher.create_remarks()
                voucher.db_set({fieldname: new, "remark": voucher.remark})

            voucher.add_comment(
                "Comment",
                _("Reference No changed from {0} to {1} via Bank Clearance Tool").format(old or "-", new),
            )
            voucher.repost_accounting_entries()
            updated.append(get_link_to_form(voucher.doctype, voucher.name))

        frappe.msgprint(
            _("Reference No updated and the ledger is being reposted in the background for: {0}").format(
                ", ".join(updated)
            ),
            title=_("Reference No Updated"),
            indicator="green",
        )
        return True

    def get_reference_changes(self):
        """[(row, old reference)] for rows whose reference differs from the voucher's."""
        changes = []
        for row in self.payment_entries:
            new = (row.cheque_number or "").strip()
            fieldname = REFERENCE_FIELDS.get(row.payment_document)
            if fieldname:
                old = frappe.db.get_value(row.payment_document, row.payment_entry, fieldname)
            else:
                # Not stored anywhere this tool writes to; only a value the
                # user typed over (vs. the one loaded) counts as a change.
                old = row.get("custom_original_reference_no")
                if old is None:
                    continue
            if new == (old or "").strip():
                continue

            if not fieldname:
                frappe.throw(
                    _("Row {0}: The reference can only be changed on Payment Entry and Journal Entry rows.").format(
                        row.idx
                    ),
                    title=_("Reference Not Editable"),
                )
            if not new:
                frappe.throw(
                    _("Row {0}: Cheque / Reference No can't be blank.").format(row.idx),
                    title=_("Missing Reference No"),
                )
            changes.append((row, old))
        return changes

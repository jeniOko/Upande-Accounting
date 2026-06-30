# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, today


class WithholdingPaymentEntry(Document):

	def validate(self):
		self._recalculate_paid_amount()
		self._validate_entries()

	def before_submit(self):
		if not self.paid_from:
			frappe.throw(_("Paid From (Bank / Cash) is mandatory before submitting."))
		if not self.paid_to:
			frappe.throw(_("Paid To (Withholding Account) is mandatory before submitting."))
		if not self.reference_number:
			frappe.throw(_("Reference / Cheque Number is mandatory before submitting."))
		if not self.reference_date:
			frappe.throw(_("Reference Date is mandatory before submitting."))

	def on_submit(self):
		if not self.withholding_entries:
			frappe.throw(_("Please fetch outstanding withholding records before submitting."))
		je = self._create_journal_entry()
		self.db_set("journal_entry", je.name)
		self._update_wtm_status("Paid", je.name)

	def on_cancel(self):
		if self.journal_entry:
			je = frappe.get_doc("Journal Entry", self.journal_entry)
			if je.docstatus == 1:
				je.cancel()
		self._update_wtm_status("Unpaid", None)

	# ------------------------------------------------------------------

	def _recalculate_paid_amount(self):
		self.paid_amount = sum(flt(r.withheld_amount) for r in (self.withholding_entries or []))

	def _validate_entries(self):
		for row in self.withholding_entries or []:
			wtm = frappe.db.get_value(
				"Withholding Tax Management",
				row.wtm_reference,
				["withholding_account", "company"],
				as_dict=True,
			)
			if not wtm:
				frappe.throw(_("WTM record {0} not found.").format(row.wtm_reference))
			if wtm.withholding_account != self.paid_to:
				frappe.throw(
					_(
						"Row {0}: WTM record <b>{1}</b> uses account <b>{2}</b> "
						"which does not match the Paid To account <b>{3}</b>."
					).format(row.idx, row.wtm_reference, wtm.withholding_account, self.paid_to)
				)

	def _create_journal_entry(self):
		cost_center = frappe.db.get_value("Company", self.company, "cost_center")

		je = frappe.new_doc("Journal Entry")
		je.voucher_type  = "Excise Entry"
		je.posting_date  = self.posting_date
		je.company       = self.company
		je.cheque_no     = self.reference_number or ""
		je.cheque_date   = self.reference_date or self.posting_date
		je.remark        = self.remarks or "Withholding Tax Payment to KRA"
		je.user_remark   = self.remarks or "Withholding Tax Payment to KRA"

		je.append("accounts", {
			"account":                    self.paid_to,
			"debit_in_account_currency":  flt(self.paid_amount),
			"debit":                      flt(self.paid_amount),
			"cost_center":                cost_center,
		})
		je.append("accounts", {
			"account":                     self.paid_from,
			"credit_in_account_currency":  flt(self.paid_amount),
			"credit":                      flt(self.paid_amount),
			"cost_center":                 cost_center,
		})

		je.insert(ignore_permissions=True)
		je.submit()
		return je

	def _update_wtm_status(self, status, je_name):
		for row in self.withholding_entries:
			wtm = frappe.get_doc("Withholding Tax Management", row.wtm_reference)
			wtm.payment_status = status
			if status == "Paid":
				wtm.payment_date  = self.posting_date
				wtm.journal_entry = je_name
				if self.reference_number:
					wtm.prn_number = self.reference_number
			else:
				wtm.payment_date  = None
				wtm.journal_entry = None
			wtm.save(ignore_permissions=True)


# ---------------------------------------------------------------------------
# Whitelisted API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_outstanding_wtm_records(paid_to_account, company):
	"""
	Return WTM records that are Invoice Paid, still Unpaid to KRA, and belong
	to the given withholding account + company.
	"""
	return frappe.get_all(
		"Withholding Tax Management",
		filters={
			"suggested_for_payment": 1,
			"payment_status":        "Unpaid",
			"withholding_account":   paid_to_account,
			"company":               company,
		},
		fields=["name", "supplier", "invoice_number", "withholding_category", "withheld_amount"],
		order_by="creation asc",
	)


@frappe.whitelist()
def create_from_wtm(wtm_names):
	"""
	Create a draft Withholding Payment Entry pre-filled with the given WTM records.
	Validates eligibility and that all records share the same company and
	withholding account. Returns the new document name.
	"""
	if isinstance(wtm_names, str):
		wtm_names = json.loads(wtm_names)

	if not wtm_names:
		frappe.throw(_("No records provided."))

	wtm_docs = [frappe.get_doc("Withholding Tax Management", n) for n in wtm_names]

	companies = {d.company for d in wtm_docs}
	accounts  = {d.withholding_account for d in wtm_docs}

	if len(companies) > 1:
		frappe.throw(_("All selected records must belong to the same company."))
	if len(accounts) > 1:
		frappe.throw(_("All selected records must share the same withholding account."))

	invalid = []
	for d in wtm_docs:
		if not d.suggested_for_payment:
			invalid.append("{} (Invoice not yet paid)".format(d.name))
		elif d.payment_status != "Unpaid":
			invalid.append("{} (Withholding already remitted)".format(d.name))
	if invalid:
		frappe.throw(
			_("The following records cannot be included:<br>{0}").format("<br>".join(invalid))
		)

	wpe = frappe.new_doc("Withholding Payment Entry")
	wpe.company      = wtm_docs[0].company
	wpe.paid_to      = wtm_docs[0].withholding_account
	wpe.posting_date = today()

	for d in wtm_docs:
		wpe.append("withholding_entries", {
			"wtm_reference":       d.name,
			"supplier":            d.supplier,
			"invoice_number":      d.invoice_number,
			"withholding_category": d.withholding_category,
			"withheld_amount":     d.withheld_amount,
		})

	wpe.paid_amount = sum(flt(d.withheld_amount) for d in wtm_docs)
	wpe.insert(ignore_permissions=True, ignore_mandatory=True)
	return wpe.name

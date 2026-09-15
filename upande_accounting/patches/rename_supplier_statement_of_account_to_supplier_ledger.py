import frappe


def execute():
	"""Rename the standard Report doc to match the renamed report module."""
	if frappe.db.exists("Report", "Supplier Statement Of Account") and not frappe.db.exists(
		"Report", "Supplier Ledger"
	):
		frappe.rename_doc("Report", "Supplier Statement Of Account", "Supplier Ledger", force=True)

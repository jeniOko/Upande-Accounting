import frappe
from frappe import _

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_dimensions


@frappe.whitelist()
def get_relatable_dimensions():
	"""Accounting dimensions (incl. Project) that a Cost Center can be linked to."""
	dimensions = get_dimensions(with_cost_center_and_project=True)[0]
	return [
		{
			"fieldname": d.fieldname,
			"document_type": d.document_type,
			"label": d.get("label") or d.document_type,
		}
		for d in dimensions
		if d.document_type != "Cost Center"
	]


def validate_related_dimensions(doc, method=None):
	allowed = {d["document_type"] for d in get_relatable_dimensions()}
	seen = set()

	for row in doc.get("custom_related_dimensions") or []:
		if row.accounting_dimension not in allowed:
			frappe.throw(
				_("Row #{0}: {1} is not a valid accounting dimension to relate to a Cost Center.").format(
					row.idx, frappe.bold(row.accounting_dimension)
				)
			)

		key = (row.accounting_dimension, row.dimension_value)
		if key in seen:
			frappe.throw(
				_("Row #{0}: {1} {2} is already linked to this Cost Center.").format(
					row.idx, row.accounting_dimension, frappe.bold(row.dimension_value)
				)
			)
		seen.add(key)

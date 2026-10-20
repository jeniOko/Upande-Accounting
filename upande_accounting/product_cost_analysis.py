"""Item configuration for the Product Cost Analysis report.

Items with Track Cost Analysis ticked list their Cost Sources (cost centers or accounting
dimension values) in the reused Cost Center Related Dimension child table. The report itself
lives in upande_accounting_customizations/report/product_cost_analysis.
"""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import get_accounting_dimensions

FIXED_PERCENT = "Fixed %"
COST_SOURCES_FIELD = "cost_dimensions"


@frappe.whitelist()
def get_cost_source_dimensions():
	"""Doctypes an Item's Cost Sources may point to: Cost Center, Project and every enabled
	Accounting Dimension. Each entry carries the fieldname the dimension uses on GL Entry."""
	dimensions = [
		{"document_type": "Cost Center", "fieldname": "cost_center", "label": _("Cost Center")},
		{"document_type": "Project", "fieldname": "project", "label": _("Project")},
	]
	for d in get_accounting_dimensions(as_list=False):
		if d.document_type not in ("Cost Center", "Project"):
			dimensions.append(
				{"document_type": d.document_type, "fieldname": d.fieldname, "label": d.label or d.document_type}
			)
	return dimensions


def validate_item(doc, method=None):
	if not doc.get("track_cost_analysis"):
		return

	doc.product_cost_classification = (doc.product_cost_classification or "").strip()
	if not doc.reporting_uom:
		doc.reporting_uom = doc.stock_uom
	if not flt(doc.reporting_conversion_factor):
		doc.reporting_conversion_factor = 1

	missing = []
	if not doc.product_cost_classification:
		missing.append(_("Product Cost Classification"))
	if not doc.production_entry_type:
		missing.append(_("Production Stock Entry Type"))
	if not doc.get(COST_SOURCES_FIELD):
		missing.append(_("at least one Cost Source"))
	if missing:
		frappe.throw(
			_("Track Cost Analysis is ticked, so this item needs: {0}").format(", ".join(missing)),
			title=_("Cost Analysis Incomplete"),
		)

	if flt(doc.reporting_conversion_factor) < 0:
		frappe.throw(_("Cost Analysis Conversion Factor must be greater than zero."))

	if doc.allocation_basis == FIXED_PERCENT:
		pct = flt(doc.allocation_percentage)
		if pct <= 0 or pct > 100:
			frappe.throw(
				_("Allocation % must be more than 0 and at most 100 when the allocation basis is Fixed %.")
			)

	validate_cost_sources(doc)
	warn_cost_centers_outside_production_companies(doc)


def validate_cost_sources(doc):
	allowed = {d["document_type"] for d in get_cost_source_dimensions()}
	seen = set()

	for row in doc.get(COST_SOURCES_FIELD):
		if row.accounting_dimension not in allowed:
			frappe.throw(
				_(
					"Cost Sources row #{0}: {1} must be Cost Center, Project or an enabled Accounting Dimension."
				).format(row.idx, frappe.bold(row.accounting_dimension))
			)
		if not row.dimension_value or not frappe.db.exists(row.accounting_dimension, row.dimension_value):
			frappe.throw(
				_("Cost Sources row #{0}: {1} {2} does not exist.").format(
					row.idx, row.accounting_dimension, frappe.bold(row.dimension_value)
				)
			)

		key = (row.accounting_dimension, row.dimension_value)
		if key in seen:
			frappe.throw(
				_("Cost Sources row #{0}: {1} {2} is listed more than once.").format(
					row.idx, row.accounting_dimension, frappe.bold(row.dimension_value)
				)
			)
		seen.add(key)


def warn_cost_centers_outside_production_companies(doc):
	"""Allowed, but usually a mistake: a cost source in a company where this item is never produced."""
	cost_centers = [
		row.dimension_value for row in doc.get(COST_SOURCES_FIELD) if row.accounting_dimension == "Cost Center"
	]
	if not cost_centers or doc.is_new():
		return

	cc_company = dict(
		frappe.get_all(
			"Cost Center", filters={"name": ["in", cost_centers]}, fields=["name", "company"], as_list=True
		)
	)
	se = frappe.qb.DocType("Stock Entry")
	sed = frappe.qb.DocType("Stock Entry Detail")
	produced_in = {
		r[0]
		for r in frappe.qb.from_(sed)
		.join(se)
		.on(se.name == sed.parent)
		.select(se.company)
		.distinct()
		.where(
			(sed.item_code == doc.name)
			& (se.docstatus == 1)
			& (se.stock_entry_type == doc.production_entry_type)
			& (sed.t_warehouse.isnotnull())
			& (sed.t_warehouse != "")
		)
		.run()
	}
	outside = [cc for cc in cost_centers if cc_company.get(cc) not in produced_in]
	if produced_in and outside:
		frappe.msgprint(
			_(
				"These cost sources belong to a company where {0} has no {1} entries: {2}. Their costs will only show if the item is produced there."
			).format(
				frappe.bold(doc.name),
				frappe.bold(doc.production_entry_type),
				", ".join(frappe.bold(cc) for cc in outside),
			),
			title=_("Cost Source Company"),
			indicator="orange",
		)

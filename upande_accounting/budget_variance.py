import frappe
from frappe import _
from frappe.desk import query_report
from frappe.model.utils import render_include
from frappe.utils import flt

ERPNEXT_BUDGET_VARIANCE_REPORT = "Budget Variance Report"


def get_income_accounts(accounts):
	"""Income accounts are credit-natured, so their actuals are reported as credit - debit."""
	if not accounts:
		return set()
	return set(
		frappe.get_all(
			"Account",
			filters={"name": ["in", list(accounts)], "root_type": "Income"},
			pluck="name",
		)
	)


def get_variance(budget, actual, is_income):
	"""Positive when favourable: under budget for expenses, over budget for income."""
	return flt(actual) - flt(budget) if is_income else flt(budget) - flt(actual)


# Adds the "Comprehensive Report" button and the "+" on income variances to ERPNext's
# report, keeping its own onload and formatter.
SIMPLE_REPORT_SCRIPT = """
{% include "upande_accounting/public/js/budget_variance.js" %}

(() => {
	const settings = frappe.query_reports["Budget Variance Report"];
	const onload = settings.onload;
	settings.onload = function (report) {
		onload && onload.apply(this, arguments);
		upande_accounting.budget_variance.add_switch_button(report);
	};
	const formatter = settings.formatter;
	settings.formatter = function (value, row, column, data, default_formatter) {
		value = formatter
			? formatter.apply(this, arguments)
			: default_formatter(value, row, column, data);
		return upande_accounting.budget_variance.mark_income_over_budget(value, column, data);
	};
})();
"""


@frappe.whitelist()
def get_report_script(report_name):
	"""frappe.desk.query_report.get_script, plus SIMPLE_REPORT_SCRIPT on ERPNext's Budget Variance Report."""
	result = query_report.get_script(report_name)
	if report_name == ERPNEXT_BUDGET_VARIANCE_REPORT:
		result["script"] += render_include(SIMPLE_REPORT_SCRIPT)
	return result


class ReportMixin:
	"""Shows income actuals in ERPNext's Budget Variance Report as positive values."""

	def execute_module(self, filters):
		res = super().execute_module(filters)
		if self.name == ERPNEXT_BUDGET_VARIANCE_REPORT:
			res = list(res)
			flip_income_actuals(res, frappe._dict(filters))
		return res


def flip_income_actuals(res, filters):
	from erpnext.accounts.report.budget_variance_report.budget_variance_report import (
		build_comparison_chart_data,
	)

	columns, data = res[0], res[1]
	if not data:
		return

	income_accounts = get_income_accounts({row.get("account") for row in data})
	if not income_accounts:
		return

	# After the dimension and account columns, every period (and the totals) is a
	# Budget / Actual / Variance triple. Paired by position since labels are translated.
	fieldnames = [col["fieldname"] for col in columns[2:]]
	triples = [fieldnames[i : i + 3] for i in range(0, len(fieldnames) - 2, 3)]

	columns.append({"label": _("Is Income"), "fieldname": "is_income", "fieldtype": "Check", "hidden": 1})
	for row in data:
		row["is_income"] = int(row.get("account") in income_accounts)
		if not row["is_income"]:
			continue
		for budget, actual, variance in triples:
			row[actual] = -flt(row.get(actual)) or 0.0
			row[variance] = get_variance(row.get(budget), row[actual], is_income=True)

	if len(res) > 3 and res[3]:
		res[3] = build_comparison_chart_data(filters, columns, data)

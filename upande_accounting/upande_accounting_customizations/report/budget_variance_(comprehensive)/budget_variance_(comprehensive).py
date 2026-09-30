# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_months, flt, formatdate, get_first_day, getdate

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_dimension_with_children,
	get_dimensions,
)
from erpnext.controllers.trends import get_period_date_ranges

RELATED_DIMENSION_DOCTYPE = "Cost Center Related Dimension"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dimension_fields = get_dimension_fields()
	validate_filters(filters)

	periods = get_periods(filters)
	columns = get_columns(periods)

	links = get_cost_center_links(filters.company)
	budgets = select_budgets(get_budgets(filters, dimension_fields), filters, dimension_fields, links)
	if not budgets:
		return columns, [], None, None

	budget_map = build_budget_map(budgets)
	actual_map = build_actual_map(budgets, filters, dimension_fields, periods)
	data = build_data(budget_map, actual_map, periods, filters, links)

	return columns, data, None, get_chart_data(data, periods)


def get_dimension_fields():
	"""{document_type: fieldname} for every budgetable dimension, Cost Center and Project included."""
	return {
		d.document_type: d.fieldname for d in get_dimensions(with_cost_center_and_project=True)[0]
	}


def validate_filters(filters):
	from_start = frappe.get_cached_value("Fiscal Year", filters.from_fiscal_year, "year_start_date")
	to_start = frappe.get_cached_value("Fiscal Year", filters.to_fiscal_year, "year_start_date")
	if getdate(from_start) > getdate(to_start):
		frappe.throw(_("From Fiscal Year cannot be after To Fiscal Year."))


def get_periods(filters):
	from_start = frappe.get_cached_value("Fiscal Year", filters.from_fiscal_year, "year_start_date")
	to_start = frappe.get_cached_value("Fiscal Year", filters.to_fiscal_year, "year_start_date")
	fiscal_years = frappe.get_all(
		"Fiscal Year",
		filters={"year_start_date": ["between", [from_start, to_start]]},
		order_by="year_start_date",
		pluck="name",
	)

	periods = []
	for fiscal_year in fiscal_years:
		for from_date, to_date in get_period_date_ranges(filters.period, fiscal_year):
			if filters.period == "Yearly":
				label = fiscal_year
			elif filters.period == "Monthly":
				label = f"{formatdate(from_date, 'MMM')} {fiscal_year}"
			else:
				label = f"{formatdate(from_date, 'MMM')}-{formatdate(to_date, 'MMM')} {fiscal_year}"

			periods.append(
				frappe._dict(
					key=f"p{len(periods)}",
					label=label,
					from_date=getdate(from_date),
					to_date=getdate(to_date),
					months=get_month_keys(from_date, to_date),
				)
			)

	return periods


def get_month_keys(start_date, end_date):
	keys = []
	current = get_first_day(start_date)
	end_date = getdate(end_date)
	while current <= end_date:
		keys.append((current.year, current.month))
		current = add_months(current, 1)
	return keys


def get_cost_center_links(company):
	"""{cost_center: {dimension_doctype: {values}}} from the Cost Center's Related Dimensions table."""
	rows = frappe.db.sql(
		"""
		SELECT rd.parent AS cost_center, rd.accounting_dimension, rd.dimension_value
		FROM `tabCost Center Related Dimension` rd
		INNER JOIN `tabCost Center` cc ON cc.name = rd.parent
		WHERE rd.parenttype = 'Cost Center'
			AND rd.parentfield = 'custom_related_dimensions'
			AND cc.company = %s
		""",
		company,
		as_dict=True,
	)

	links = {}
	for row in rows:
		links.setdefault(row.cost_center, {}).setdefault(row.accounting_dimension, set()).add(
			row.dimension_value
		)
	return links


def get_budgets(filters, dimension_fields):
	budget_meta = frappe.get_meta("Budget")
	fields = {fieldname for fieldname in dimension_fields.values() if budget_meta.has_field(fieldname)}

	report_start = frappe.get_cached_value("Fiscal Year", filters.from_fiscal_year, "year_start_date")
	report_end = frappe.get_cached_value("Fiscal Year", filters.to_fiscal_year, "year_end_date")

	conditions = {
		"company": filters.company,
		"docstatus": 1,
		"budget_start_date": ["<=", report_end],
		"budget_end_date": [">=", report_start],
	}
	if filters.account:
		conditions["account"] = ["in", filters.account]

	budgets = frappe.db.get_all(
		"Budget",
		filters=conditions,
		fields=["name", "budget_against", "account", *fields],
	)

	for budget in budgets:
		budget.dimension = budget.get(dimension_fields.get(budget.budget_against))
	return [b for b in budgets if b.dimension]


def select_budgets(budgets, filters, dimension_fields, links):
	"""Keep budgets matching the filters, following Cost Center <-> dimension links in both directions."""
	selected_cost_centers = set(filters.cost_center or [])
	dimension_filters = {
		doctype: set(filters.get(fieldname))
		for doctype, fieldname in dimension_fields.items()
		if doctype != "Cost Center" and filters.get(fieldname)
	}

	values_linked_to_selected_ccs = {}
	for cost_center in selected_cost_centers:
		for doctype, values in links.get(cost_center, {}).items():
			values_linked_to_selected_ccs.setdefault(doctype, set()).update(values)

	def cost_center_matches(cost_center):
		if selected_cost_centers and cost_center not in selected_cost_centers:
			return False
		related = links.get(cost_center, {})
		return all(related.get(doctype, set()) & values for doctype, values in dimension_filters.items())

	def dimension_matches(doctype, value):
		if dimension_filters and value not in dimension_filters.get(doctype, ()):
			return False
		if selected_cost_centers and value not in values_linked_to_selected_ccs.get(doctype, ()):
			return False
		return True

	return [
		b
		for b in budgets
		if (
			cost_center_matches(b.dimension)
			if b.budget_against == "Cost Center"
			else dimension_matches(b.budget_against, b.dimension)
		)
	]


def build_budget_map(budgets):
	"""{(budget_against, dimension, account): {(year, month): amount}}"""
	distributions = frappe.db.get_all(
		"Budget Distribution",
		filters={"parenttype": "Budget", "parent": ["in", [b.name for b in budgets]]},
		fields=["parent", "start_date", "end_date", "amount"],
	)
	distributions_by_budget = {}
	for row in distributions:
		distributions_by_budget.setdefault(row.parent, []).append(row)

	budget_map = {}
	for budget in budgets:
		monthly = budget_map.setdefault((budget.budget_against, budget.dimension, budget.account), {})
		for row in distributions_by_budget.get(budget.name, []):
			months = get_month_keys(row.start_date, row.end_date)
			for month in months:
				monthly[month] = monthly.get(month, 0) + flt(row.amount) / len(months)

	return budget_map


def build_actual_map(budgets, filters, dimension_fields, periods):
	"""{(budget_against, dimension, account): {(year, month): debit - credit}}

	Tree dimensions (e.g. Cost Center) include their descendants, matching ERPNext budget control.
	"""
	actual_map = {}
	budgets_by_type = {}
	for budget in budgets:
		budgets_by_type.setdefault(budget.budget_against, []).append(budget)

	for doctype, type_budgets in budgets_by_type.items():
		fieldname = dimension_fields[doctype]
		is_tree = frappe.get_meta(doctype).is_tree

		members = {}
		for value in {b.dimension for b in type_budgets}:
			members[value] = get_dimension_with_children(doctype, value) if is_tree else [value]

		all_members = {m for values in members.values() for m in values}
		accounts = {b.account for b in type_budgets}

		gl_rows = frappe.db.sql(
			f"""
			SELECT
				account,
				`{fieldname}` AS dimension,
				YEAR(posting_date) AS year,
				MONTH(posting_date) AS month,
				SUM(debit) - SUM(credit) AS amount
			FROM `tabGL Entry`
			WHERE company = %(company)s
				AND is_cancelled = 0
				AND posting_date BETWEEN %(from_date)s AND %(to_date)s
				AND account IN %(accounts)s
				AND `{fieldname}` IN %(members)s
			GROUP BY account, `{fieldname}`, YEAR(posting_date), MONTH(posting_date)
			""",
			{
				"company": filters.company,
				"from_date": periods[0].from_date,
				"to_date": periods[-1].to_date,
				"accounts": tuple(accounts),
				"members": tuple(all_members),
			},
			as_dict=True,
		)

		gl_index = {}
		for row in gl_rows:
			monthly = gl_index.setdefault((row.account, row.dimension), {})
			monthly[(row.year, row.month)] = flt(row.amount)

		for budget in type_budgets:
			key = (doctype, budget.dimension, budget.account)
			if key in actual_map:
				continue
			monthly = actual_map.setdefault(key, {})
			for member in members[budget.dimension]:
				for month, amount in gl_index.get((budget.account, member), {}).items():
					monthly[month] = monthly.get(month, 0) + amount

	return actual_map


def build_data(budget_map, actual_map, periods, filters, links):
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	show_cumulative = filters.show_cumulative and filters.period != "Yearly"

	cost_centers_by_value = {}
	for cost_center, related in links.items():
		for doctype, values in related.items():
			for value in values:
				cost_centers_by_value.setdefault((doctype, value), set()).add(cost_center)

	data = []
	for key in sorted(budget_map, key=lambda k: (k[0] != "Cost Center", k[0], k[1], k[2])):
		budget_against, dimension, account = key
		monthly_budget = budget_map[key]
		monthly_actual = actual_map.get(key, {})

		row = frappe._dict(
			budget_against=budget_against,
			dimension=dimension,
			related_dimensions=get_related_label(budget_against, dimension, links, cost_centers_by_value),
			account=account,
			currency=currency,
		)

		total_budget = total_actual = 0
		for period in periods:
			period_budget = sum(monthly_budget.get(m, 0) for m in period.months)
			period_actual = sum(monthly_actual.get(m, 0) for m in period.months)
			total_budget += period_budget
			total_actual += period_actual

			shown_budget, shown_actual = (
				(total_budget, total_actual) if show_cumulative else (period_budget, period_actual)
			)
			row[f"budget_{period.key}"] = shown_budget
			row[f"actual_{period.key}"] = shown_actual
			row[f"variance_{period.key}"] = shown_budget - shown_actual

		row.total_budget = total_budget
		row.total_actual = total_actual
		row.total_variance = total_budget - total_actual
		row.utilization = (total_actual / total_budget * 100) if total_budget else 0
		data.append(row)

	return data


def get_related_label(budget_against, dimension, links, cost_centers_by_value):
	if budget_against == "Cost Center":
		related = links.get(dimension, {})
		return "; ".join(
			f"{_(doctype)}: {', '.join(sorted(values))}" for doctype, values in sorted(related.items())
		)

	cost_centers = cost_centers_by_value.get((budget_against, dimension))
	return f"{_('Cost Center')}: {', '.join(sorted(cost_centers))}" if cost_centers else ""


def get_columns(periods):
	columns = [
		{
			"label": _("Budget Against"),
			"fieldname": "budget_against",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 120,
		},
		{
			"label": _("Dimension"),
			"fieldname": "dimension",
			"fieldtype": "Dynamic Link",
			"options": "budget_against",
			"width": 180,
		},
		{
			"label": _("Account"),
			"fieldname": "account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 280,
		},
		{
			"label": _("% Utilized"),
			"fieldname": "utilization",
			"fieldtype": "Percent",
			"width": 100,
		},
	]

	for period in periods:
		for prefix, label in (("budget", _("Budget")), ("actual", _("Actual")), ("variance", _("Variance"))):
			columns.append(
				{
					"label": f"{label} ({period.label})",
					"fieldname": f"{prefix}_{period.key}",
					"fieldtype": "Currency",
					"options": "currency",
					"width": 150,
				}
			)

	if len(periods) > 1:
		for fieldname, label in (
			("total_budget", _("Total Budget")),
			("total_actual", _("Total Actual")),
			("total_variance", _("Total Variance")),
		):
			columns.append(
				{
					"label": label,
					"fieldname": fieldname,
					"fieldtype": "Currency",
					"options": "currency",
					"width": 150,
				}
			)

	columns.append(
		{
			"label": _("Related Dimensions"),
			"fieldname": "related_dimensions",
			"fieldtype": "Data",
			"width": 220,
		}
	)
	columns.append({"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1})
	return columns


def get_chart_data(data, periods):
	return {
		"data": {
			"labels": [p.label for p in periods],
			"datasets": [
				{
					"name": _("Budget"),
					"values": [sum(flt(r.get(f"budget_{p.key}")) for r in data) for p in periods],
				},
				{
					"name": _("Actual"),
					"values": [sum(flt(r.get(f"actual_{p.key}")) for r in data) for p in periods],
				},
			],
		},
		"type": "bar",
		"fieldtype": "Currency",
	}

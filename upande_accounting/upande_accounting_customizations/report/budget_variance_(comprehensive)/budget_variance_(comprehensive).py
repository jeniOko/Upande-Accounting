# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, add_months, flt, formatdate, get_first_day, get_last_day, getdate

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
	get_dimension_with_children,
	get_dimensions,
)
from erpnext.controllers.trends import get_period_date_ranges

from upande_accounting.budget_variance import get_variance

RELATED_DIMENSION_DOCTYPE = "Cost Center Related Dimension"
MONTHS_PER_PERIOD = {"Monthly": 1, "Quarterly": 3, "Half-Yearly": 6, "Yearly": 12}
# Group By options besides the accounting dimensions (Cost Center, Farm, Project, ...).
ACCOUNT_GROUPINGS = ("Account", "Parent Account", "Account Type", "Budget Against")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	dimension_fields = get_dimension_fields()
	set_date_range(filters)

	periods = get_periods(filters)
	columns = get_columns(periods, filters)

	links = get_cost_center_links(filters.company)
	all_budgets = get_budgets(filters, dimension_fields)
	budgets = select_budgets(all_budgets, filters, dimension_fields, links)
	accounts = get_account_details({b.account for b in budgets})
	if filters.root_type:
		budgets = [b for b in budgets if accounts[b.account].root_type == filters.root_type]

	unbudgeted = {}
	if filters.show_unbudgeted and filters.root_type != "Income":
		# Coverage is checked against every budget, so spending under a budgeted parent cost
		# center isn't reported as unbudgeted when only the child is selected.
		unbudgeted = get_unbudgeted_actuals(all_budgets, filters, dimension_fields, links)
		accounts.update(get_account_details({key[2] for key in unbudgeted}))
	if not (budgets or unbudgeted):
		return columns, [], None, None, None

	budget_map = build_budget_map(budgets) if budgets else {}
	actual_map = build_actual_map(budgets, filters, dimension_fields, accounts)
	budget_map.update(dict.fromkeys(unbudgeted, {}))
	actual_map.update(unbudgeted)
	lines = build_lines(budget_map, actual_map, periods, filters, links, accounts, set(unbudgeted))
	if filters.only_unfavourable:
		lines = [line for line in lines if line.total_variance < 0]
	if not lines:
		return columns, [], None, None, None

	value_fields = get_value_fields(periods)
	if filters.group_by:
		grouped = group_lines(lines, filters.group_by, links, value_fields)
		groups = [group for group, _members in grouped]
		data = []
		for group, members in grouped:
			data.append(group)
			if filters.show_budget_lines:
				data.extend(members)
	else:
		groups = None
		data = list(lines)

	total = aggregate(lines, value_fields, is_total=1)
	total["group" if filters.group_by else "budget_against"] = _("Total")
	data.append(total)
	if filters.group_by and filters.show_budget_lines:
		for row in data:
			row.indent = 0 if (row.is_group or row.is_total) else 1

	currency = lines[0].currency
	return (
		columns,
		data,
		None,
		get_chart_data(lines, groups, periods),
		get_report_summary(lines, currency),
	)


def get_dimension_fields():
	"""{document_type: fieldname} for every budgetable dimension, Cost Center and Project included."""
	return {
		d.document_type: d.fieldname for d in get_dimensions(with_cost_center_and_project=True)[0]
	}


def set_date_range(filters):
	"""Resolve filters.from_date / to_date from the fiscal years or the date range picked."""
	if filters.filter_based_on == "Date Range":
		if not (filters.from_date and filters.to_date):
			frappe.throw(_("From Date and To Date are required."))
		filters.from_date, filters.to_date = getdate(filters.from_date), getdate(filters.to_date)
		if filters.from_date > filters.to_date:
			frappe.throw(_("From Date cannot be after To Date."))
		return

	if not (filters.from_fiscal_year and filters.to_fiscal_year):
		frappe.throw(_("From Fiscal Year and To Fiscal Year are required."))
	from_start, from_end = frappe.get_cached_value(
		"Fiscal Year", filters.from_fiscal_year, ["year_start_date", "year_end_date"]
	)
	to_start, to_end = frappe.get_cached_value(
		"Fiscal Year", filters.to_fiscal_year, ["year_start_date", "year_end_date"]
	)
	if getdate(from_start) > getdate(to_start):
		frappe.throw(_("From Fiscal Year cannot be after To Fiscal Year."))
	filters.from_date, filters.to_date = getdate(from_start), getdate(to_end)


def get_periods(filters):
	if filters.filter_based_on == "Date Range":
		return get_date_range_periods(filters)

	fiscal_years = frappe.get_all(
		"Fiscal Year",
		filters={"year_start_date": ["between", [filters.from_date, filters.to_date]]},
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
			periods.append(make_period(len(periods), label, from_date, to_date))

	return periods


def get_date_range_periods(filters):
	"""Calendar-month aligned periods; the first and last are cut at the range's edges."""
	periods = []
	start = filters.from_date
	while start <= filters.to_date:
		end = add_days(add_months(get_first_day(start), MONTHS_PER_PERIOD[filters.period]), -1)
		end = min(getdate(end), filters.to_date)

		if filters.period == "Monthly":
			label = formatdate(start, "MMM yyyy")
		elif start.year == end.year:
			label = f"{formatdate(start, 'MMM')}-{formatdate(end, 'MMM yyyy')}"
		else:
			label = f"{formatdate(start, 'MMM yyyy')}-{formatdate(end, 'MMM yyyy')}"

		periods.append(make_period(len(periods), label, start, end))
		start = getdate(add_days(end, 1))
	return periods


def make_period(index, label, from_date, to_date):
	return frappe._dict(
		key=f"p{index}",
		label=label,
		from_date=getdate(from_date),
		to_date=getdate(to_date),
		months=get_month_weights(from_date, to_date),
	)


def get_month_weights(start_date, end_date):
	"""{(year, month): share of the month inside the range}, used to prorate monthly budgets."""
	start_date, end_date = getdate(start_date), getdate(end_date)
	weights = {}
	current = get_first_day(start_date)
	while current <= end_date:
		month_end = getdate(get_last_day(current))
		days_in_range = (min(month_end, end_date) - max(current, start_date)).days + 1
		weights[(current.year, current.month)] = days_in_range / month_end.day
		current = add_months(current, 1)
	return weights


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

	conditions = {
		"company": filters.company,
		"docstatus": 1,
		"budget_start_date": ["<=", filters.to_date],
		"budget_end_date": [">=", filters.from_date],
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


def get_account_details(accounts):
	if not accounts:
		return {}
	return {
		a.name: a
		for a in frappe.get_all(
			"Account",
			filters={"name": ["in", list(accounts)]},
			fields=["name", "root_type", "parent_account"],
		)
	}


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


def build_actual_map(budgets, filters, dimension_fields, accounts):
	"""{(budget_against, dimension, account): {(year, month): amount}}

	Amounts are debit - credit, except for income accounts which are credit - debit so that
	income actuals show as positive. Tree dimensions (e.g. Cost Center) include their
	descendants, matching ERPNext budget control.
	"""
	actual_map = {}
	budgets_by_type = {}
	for budget in budgets:
		budgets_by_type.setdefault(budget.budget_against, []).append(budget)

	for doctype, type_budgets in budgets_by_type.items():
		fieldname = dimension_fields[doctype]

		members = {value: get_members(doctype, value) for value in {b.dimension for b in type_budgets}}

		all_members = {m for values in members.values() for m in values}
		type_accounts = {b.account for b in type_budgets}

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
				"from_date": filters.from_date,
				"to_date": filters.to_date,
				"accounts": tuple(type_accounts),
				"members": tuple(all_members),
			},
			as_dict=True,
		)

		gl_index = {}
		for row in gl_rows:
			monthly = gl_index.setdefault((row.account, row.dimension), {})
			sign = -1 if accounts[row.account].root_type == "Income" else 1
			monthly[(row.year, row.month)] = sign * flt(row.amount) or 0.0

		for budget in type_budgets:
			key = (doctype, budget.dimension, budget.account)
			if key in actual_map:
				continue
			monthly = actual_map.setdefault(key, {})
			for member in members[budget.dimension]:
				for month, amount in gl_index.get((budget.account, member), {}).items():
					monthly[month] = monthly.get(month, 0) + amount

	return actual_map


def get_members(doctype, value):
	"""The dimension value plus, for tree dimensions (e.g. Cost Center), its descendants."""
	if frappe.get_meta(doctype).is_tree:
		return get_dimension_with_children(doctype, value)
	return [value]


def get_unbudgeted_actuals(budgets, filters, dimension_fields, links):
	"""{("Cost Center", cost_center, account): {(year, month): amount}} for expense GL entries
	that no budget line covers, matched the way budget actuals are (dimension and descendants)."""
	coverage = {}
	for budget in budgets:
		coverage.setdefault(budget.account, []).append(
			(dimension_fields[budget.budget_against], set(get_members(budget.budget_against, budget.dimension)))
		)

	gl_meta = frappe.get_meta("GL Entry")
	other_fields = [
		fieldname
		for fieldname in dimension_fields.values()
		if fieldname != "cost_center" and gl_meta.has_field(fieldname)
	]
	conditions = ""
	values = {"company": filters.company, "from_date": filters.from_date, "to_date": filters.to_date}
	if filters.account:
		conditions += " AND gle.account IN %(accounts)s"
		values["accounts"] = tuple(filters.account)
	if filters.cost_center:
		conditions += " AND gle.cost_center IN %(cost_centers)s"
		values["cost_centers"] = tuple(
			{m for cc in filters.cost_center for m in get_members("Cost Center", cc)}
		)

	gl_rows = frappe.db.sql(
		f"""
		SELECT
			gle.account,
			gle.cost_center,
			{"".join(f"gle.`{f}`, " for f in other_fields)}
			YEAR(gle.posting_date) AS year,
			MONTH(gle.posting_date) AS month,
			SUM(gle.debit) - SUM(gle.credit) AS amount
		FROM `tabGL Entry` gle
		INNER JOIN `tabAccount` acc ON acc.name = gle.account
		WHERE gle.company = %(company)s
			AND gle.is_cancelled = 0
			AND gle.posting_date BETWEEN %(from_date)s AND %(to_date)s
			AND acc.root_type = 'Expense'
			{conditions}
		GROUP BY gle.account, gle.cost_center, {"".join(f"gle.`{f}`, " for f in other_fields)}
			YEAR(gle.posting_date), MONTH(gle.posting_date)
		""",
		values,
		as_dict=True,
	)

	# Other dimension filters (e.g. Farm) match the entry's own value or its cost center's links.
	dimension_filters = {
		doctype: set(filters.get(fieldname))
		for doctype, fieldname in dimension_fields.items()
		if doctype != "Cost Center" and filters.get(fieldname)
	}

	unbudgeted = {}
	for row in gl_rows:
		if any(row.get(fieldname) in members for fieldname, members in coverage.get(row.account, [])):
			continue
		related = links.get(row.cost_center, {})
		if not all(
			row.get(dimension_fields[doctype]) in selected or related.get(doctype, set()) & selected
			for doctype, selected in dimension_filters.items()
		):
			continue
		monthly = unbudgeted.setdefault(("Cost Center", row.cost_center or "", row.account), {})
		monthly[(row.year, row.month)] = monthly.get((row.year, row.month), 0) + flt(row.amount)

	return {key: monthly for key, monthly in unbudgeted.items() if flt(sum(monthly.values()), 2)}


def build_lines(budget_map, actual_map, periods, filters, links, accounts, unbudgeted=()):
	"""One row per budget line: (budget against, dimension, account), unbudgeted lines last."""
	currency = frappe.get_cached_value("Company", filters.company, "default_currency")
	show_cumulative = filters.show_cumulative and filters.period != "Yearly"
	cost_centers_by_value = get_cost_centers_by_value(links)

	lines = []
	for key in sorted(budget_map, key=lambda k: (k in unbudgeted, k[0] != "Cost Center", k[0], k[1], k[2])):
		budget_against, dimension, account = key
		monthly_budget = budget_map[key]
		monthly_actual = actual_map.get(key, {})
		root_type = accounts[account].root_type
		is_income = int(root_type == "Income")

		row = frappe._dict(
			budget_against=budget_against,
			dimension=dimension,
			related_dimensions=get_related_label(budget_against, dimension, links, cost_centers_by_value),
			cost_centers=sorted(get_line_cost_centers(budget_against, dimension, cost_centers_by_value)),
			account=account,
			parent_account=accounts[account].parent_account,
			root_type=root_type,
			account_type=_(root_type),
			is_income=is_income,
			is_unbudgeted=int(key in unbudgeted),
			currency=currency,
		)

		total_budget = total_actual = 0
		for period in periods:
			# Budgets are prorated for months only partly in the period; actuals are already
			# limited to the report's dates.
			period_budget = sum(monthly_budget.get(m, 0) * share for m, share in period.months.items())
			period_actual = sum(monthly_actual.get(m, 0) for m in period.months)
			total_budget += period_budget
			total_actual += period_actual

			shown_budget, shown_actual = (
				(total_budget, total_actual) if show_cumulative else (period_budget, period_actual)
			)
			row[f"budget_{period.key}"] = shown_budget
			row[f"actual_{period.key}"] = shown_actual
			row[f"variance_{period.key}"] = get_variance(shown_budget, shown_actual, is_income)

		row.total_budget = total_budget
		row.total_actual = total_actual
		row.total_variance = get_variance(total_budget, total_actual, is_income)
		set_percentages(row)
		lines.append(row)

	return lines


def get_cost_centers_by_value(links):
	cost_centers_by_value = {}
	for cost_center, related in links.items():
		for doctype, values in related.items():
			for value in values:
				cost_centers_by_value.setdefault((doctype, value), set()).add(cost_center)
	return cost_centers_by_value


def get_line_cost_centers(budget_against, dimension, cost_centers_by_value):
	if budget_against == "Cost Center":
		return {dimension}
	return cost_centers_by_value.get((budget_against, dimension), set())


def get_related_label(budget_against, dimension, links, cost_centers_by_value):
	if budget_against == "Cost Center":
		related = links.get(dimension, {})
		return "; ".join(
			f"{_(doctype)}: {', '.join(sorted(values))}" for doctype, values in sorted(related.items())
		)

	cost_centers = cost_centers_by_value.get((budget_against, dimension))
	return f"{_('Cost Center')}: {', '.join(sorted(cost_centers))}" if cost_centers else ""


def get_value_fields(periods):
	fields = [f"{prefix}_{p.key}" for p in periods for prefix in ("budget", "actual", "variance")]
	return [*fields, "total_budget", "total_actual", "total_variance"]


def get_group_value(line, group_by, links):
	"""The value a budget line is grouped under, following Cost Center <-> dimension links."""
	if group_by == "Account":
		return line.account
	if group_by == "Parent Account":
		return line.parent_account
	if group_by == "Account Type":
		return line.account_type
	if group_by == "Budget Against":
		return _(line.budget_against)
	if group_by == line.budget_against:
		return line.dimension
	if group_by == "Cost Center":
		values = set(line.cost_centers)
	else:
		values = set()
		for cost_center in line.cost_centers:
			values.update(links.get(cost_center, {}).get(group_by, ()))
	return ", ".join(sorted(values))


def group_lines(lines, group_by, links, value_fields):
	lines_by_group = {}
	for line in lines:
		lines_by_group.setdefault(get_group_value(line, group_by, links) or "", []).append(line)

	# [(group row, its lines)]. Lines that can't be placed go last, under "Not Assigned".
	return [
		(
			aggregate(lines_by_group[value], value_fields, is_group=1, group=value or _("Not Assigned")),
			lines_by_group[value],
		)
		for value in sorted(lines_by_group, key=lambda v: (not v, v))
	]


def aggregate(lines, value_fields, **values):
	"""A subtotal row. Income and expense lines are combined as net (income - expense), where
	a higher actual is favourable, so the variance stays the sum of the lines' variances."""
	types = {line.is_income for line in lines}
	is_net = len(types) > 1
	row = frappe._dict(values, currency=lines[0].currency, is_income=int(is_net or 1 in types))
	row.account_type = _("Net") if is_net else lines[0].account_type

	for field in value_fields:
		if field.startswith(("variance_", "total_variance")):
			row[field] = sum(flt(line[field]) for line in lines)
		else:
			row[field] = sum(
				flt(line[field]) * (-1 if is_net and not line.is_income else 1) for line in lines
			)
	set_percentages(row, with_utilization=not is_net)
	return row


def set_percentages(row, with_utilization=True):
	budget = flt(row.total_budget)
	row.utilization = flt(flt(row.total_actual) / budget * 100, 2) if budget and with_utilization else None
	row.variance_percent = flt(flt(row.total_variance) / abs(budget) * 100, 2) if budget else None


def get_columns(periods, filters):
	columns = []
	if filters.group_by:
		columns.append(
			{
				"label": _(filters.group_by),
				"fieldname": "group",
				"fieldtype": "Data",
				"width": 220,
			}
		)

	columns += [
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
			"label": _("Type"),
			"fieldname": "account_type",
			"fieldtype": "Data",
			"width": 90,
		},
		{
			"label": _("% of Budget"),
			"fieldname": "utilization",
			"fieldtype": "Percent",
			"precision": 2,
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

	columns += [
		{
			"label": _("Variance %"),
			"fieldname": "variance_percent",
			"fieldtype": "Percent",
			"precision": 2,
			"width": 100,
		},
		{
			"label": _("Related Dimensions"),
			"fieldname": "related_dimensions",
			"fieldtype": "Data",
			"width": 220,
		},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Link", "options": "Currency", "hidden": 1},
		{"label": _("Is Income"), "fieldname": "is_income", "fieldtype": "Check", "hidden": 1},
	]
	return columns


def get_chart_data(lines, groups, periods):
	"""Budget vs actual per group when grouped, otherwise per period split into income and expense."""
	if groups:
		return {
			"data": {
				"labels": [g.group for g in groups],
				"datasets": [
					{"name": _("Budget"), "values": [g.total_budget for g in groups]},
					{"name": _("Actual"), "values": [g.total_actual for g in groups]},
				],
			},
			"type": "bar",
			"fieldtype": "Currency",
		}

	datasets = []
	for is_income, label in ((0, _("Expense")), (1, _("Income"))):
		typed = [line for line in lines if line.is_income == is_income]
		if not typed:
			continue
		for prefix, name in (("budget", _("Budget")), ("actual", _("Actual"))):
			datasets.append(
				{
					"name": f"{label} {name}",
					"values": [sum(flt(line[f"{prefix}_{p.key}"]) for line in typed) for p in periods],
				}
			)

	return {
		"data": {"labels": [p.label for p in periods], "datasets": datasets},
		"type": "bar",
		"fieldtype": "Currency",
	}


def get_report_summary(lines, currency):
	summary = []
	variances = []
	for is_income, label in ((0, _("Expense")), (1, _("Income"))):
		typed = [line for line in lines if line.is_income == is_income]
		if not typed:
			continue
		budget = sum(line.total_budget for line in typed)
		actual = sum(line.total_actual for line in typed)
		variance = sum(line.total_variance for line in typed)
		variances.append(variance)
		summary += [
			{"value": budget, "label": f"{label} {_('Budget')}", "datatype": "Currency", "currency": currency},
			{"value": actual, "label": f"{label} {_('Actual')}", "datatype": "Currency", "currency": currency},
			{
				"value": variance,
				"label": f"{label} {_('Variance')}",
				"datatype": "Currency",
				"currency": currency,
				"indicator": "Green" if variance >= 0 else "Red",
			},
		]

	unbudgeted = sum(line.total_actual for line in lines if line.is_unbudgeted)
	if unbudgeted:
		summary.append(
			{
				"value": unbudgeted,
				"label": _("Unbudgeted Expenses"),
				"datatype": "Currency",
				"currency": currency,
				"indicator": "Red",
			}
		)

	if len(variances) > 1:
		net = sum(variances)
		summary.append(
			{
				"value": net,
				"label": _("Net Variance"),
				"datatype": "Currency",
				"currency": currency,
				"indicator": "Green" if net >= 0 else "Red",
			}
		)
	return summary

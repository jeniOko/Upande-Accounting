// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

{% include "upande_accounting/public/js/budget_variance.js" %}

frappe.query_reports["Budget Variance (Comprehensive)"] = {
	filters: get_dimensional_budget_filters(),
	onload(report) {
		upande_accounting.budget_variance.add_switch_button(report);
	},
	formatter(value, row, column, data, default_formatter) {
		// Group and total labels are plain text, not links.
		if (data && (data.is_group || data.is_total) && column.fieldtype === "Link") {
			column = { ...column, fieldtype: "Data" };
		}
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		value = upande_accounting.budget_variance.mark_income_over_budget(value, column, data);
		if (data.is_group || data.is_total) value = `<b>${value}</b>`;

		const raw = data[column.fieldname];
		if (column.fieldname === "utilization" && data.is_unbudgeted) {
			return `<span style="color:var(--red-600)">${__("No Budget")}</span>`;
		}
		if (column.fieldname.includes("variance")) {
			// Variance is signed so that positive is favourable, for income and expense alike.
			if (raw < 0) value = `<span style="color:var(--red-600)">${value}</span>`;
			else if (raw > 0) value = `<span style="color:var(--green-600)">${value}</span>`;
		} else if (column.fieldname === "utilization" && !data.is_income) {
			if (raw > 100) value = `<span style="color:var(--red-600);font-weight:600">${value}</span>`;
			else if (raw >= 90) value = `<span style="color:var(--orange-600)">${value}</span>`;
		}
		return value;
	},
};

function get_dimensional_budget_filters() {
	let dimensions = [];
	frappe.call({
		method: "upande_accounting.cost_center_dimensions.get_relatable_dimensions",
		async: false,
		callback: (r) => (dimensions = r.message || []),
	});

	const company_link_options = (doctype, txt) =>
		frappe.db.get_link_options(doctype, txt, {
			company: frappe.query_report.get_filter_value("company"),
		});

	const filters = [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "filter_based_on",
			label: __("Filter Based On"),
			fieldtype: "Select",
			options: [
				{ value: "Fiscal Year", label: __("Fiscal Year") },
				{ value: "Date Range", label: __("Date Range") },
			],
			default: "Fiscal Year",
			reqd: 1,
		},
		{
			fieldname: "from_fiscal_year",
			label: __("From Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today()),
			depends_on: "eval:doc.filter_based_on == 'Fiscal Year'",
		},
		{
			fieldname: "to_fiscal_year",
			label: __("To Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today()),
			depends_on: "eval:doc.filter_based_on == 'Fiscal Year'",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[1],
			depends_on: "eval:doc.filter_based_on == 'Date Range'",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			depends_on: "eval:doc.filter_based_on == 'Date Range'",
		},
		{
			fieldname: "period",
			label: __("Period"),
			fieldtype: "Select",
			options: [
				{ value: "Monthly", label: __("Monthly") },
				{ value: "Quarterly", label: __("Quarterly") },
				{ value: "Half-Yearly", label: __("Half-Yearly") },
				{ value: "Yearly", label: __("Yearly") },
			],
			default: "Yearly",
			reqd: 1,
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "MultiSelectList",
			options: "Cost Center",
			get_data: (txt) => company_link_options("Cost Center", txt),
		},
		...dimensions.map((d) => ({
			fieldname: d.fieldname,
			label: __(d.label),
			fieldtype: "MultiSelectList",
			options: d.document_type,
			get_data: (txt) => frappe.db.get_link_options(d.document_type, txt),
		})),
		{
			fieldname: "account",
			label: __("Account"),
			fieldtype: "MultiSelectList",
			options: "Account",
			get_data: (txt) => company_link_options("Account", txt),
		},
		{
			fieldname: "root_type",
			label: __("Account Type"),
			fieldtype: "Select",
			options: [
				{ value: "", label: "" },
				{ value: "Income", label: __("Income") },
				{ value: "Expense", label: __("Expense") },
			],
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: [
				{ value: "", label: "" },
				{ value: "Account", label: __("Account") },
				{ value: "Parent Account", label: __("Parent Account") },
				{ value: "Account Type", label: __("Account Type") },
				{ value: "Budget Against", label: __("Budget Against") },
				{ value: "Cost Center", label: __("Cost Center") },
				...dimensions.map((d) => ({ value: d.document_type, label: __(d.label) })),
			],
		},
		{
			fieldname: "show_budget_lines",
			label: __("Show Budget Lines"),
			fieldtype: "Check",
			default: 1,
			depends_on: "group_by",
		},
		{
			fieldname: "show_unbudgeted",
			label: __("Show Unbudgeted Expenses"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "only_unfavourable",
			label: __("Only Unfavourable Variances"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "show_cumulative",
			label: __("Show Cumulative Amount"),
			fieldtype: "Check",
			default: 0,
		},
	];

	return filters;
}

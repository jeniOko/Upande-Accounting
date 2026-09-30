// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Budget Variance (Comprehensive)"] = {
	filters: get_dimensional_budget_filters(),
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		const raw = data[column.fieldname];
		if (column.fieldname.includes("variance")) {
			if (raw < 0) value = `<span style="color:var(--red-600)">${value}</span>`;
			else if (raw > 0) value = `<span style="color:var(--green-600)">${value}</span>`;
		} else if (column.fieldname === "utilization") {
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
			fieldname: "from_fiscal_year",
			label: __("From Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today()),
			reqd: 1,
		},
		{
			fieldname: "to_fiscal_year",
			label: __("To Fiscal Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
			default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today()),
			reqd: 1,
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
			fieldname: "show_cumulative",
			label: __("Show Cumulative Amount"),
			fieldtype: "Check",
			default: 0,
		},
	];

	return filters;
}

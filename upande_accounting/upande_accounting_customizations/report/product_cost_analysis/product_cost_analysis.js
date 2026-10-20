// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

const PCA_REPORT = "Product Cost Analysis";
const PCA_COST_FIELDS = [
	"direct_cost",
	"indirect_cost",
	"contribution_margin",
	"net_margin",
	"production_cost",
	"cost_per_unit",
];
const PCA_DRILL_TARGETS = {
	gl: "General Ledger",
	stock: "Stock Ledger",
	sales: "Item-wise Sales Register",
};

function pca_drill_target(fieldname) {
	if (PCA_COST_FIELDS.includes(fieldname)) return "gl";
	if (fieldname === "produced_qty") return "stock";
	if (fieldname === "sold_qty" || fieldname === "revenue") return "sales";
	return null;
}

frappe.query_reports[PCA_REPORT] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(frappe.datetime.add_months(frappe.datetime.get_today(), -1)),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(frappe.datetime.add_months(frappe.datetime.get_today(), -1)),
			reqd: 1,
		},
		{
			fieldname: "periodicity",
			label: __("Periodicity"),
			fieldtype: "Select",
			options: [
				{ value: "Monthly", label: __("Monthly") },
				{ value: "Quarterly", label: __("Quarterly") },
				{ value: "Half-Yearly", label: __("Half-Yearly") },
				{ value: "Yearly", label: __("Yearly") },
			],
			default: "Monthly",
			reqd: 1,
		},
		{
			fieldname: "product_cost_classification",
			label: __("Product Cost Classification"),
			fieldtype: "MultiSelectList",
			get_data: (txt) =>
				frappe
					.call({
						method: "upande_accounting.upande_accounting_customizations.report.product_cost_analysis.product_cost_analysis.get_classifications",
						args: { txt },
					})
					.then((r) => r.message || []),
		},
		{
			fieldname: "item",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
			get_query: () => ({ filters: { track_cost_analysis: 1 } }),
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: [
				{ value: "Product Cost Classification", label: __("Product Cost Classification") },
				{ value: "Item", label: __("Item") },
			],
			default: "Product Cost Classification",
		},
		{
			fieldname: "cost_center",
			label: __("Cost Center"),
			fieldtype: "Link",
			options: "Cost Center",
			get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }),
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
			get_query: () => ({ filters: { company: frappe.query_report.get_filter_value("company") } }),
		},
		{
			fieldname: "show_per_unit",
			label: __("Show Per Unit"),
			fieldtype: "Check",
		},
		{
			fieldname: "show_cost_breakdown",
			label: __("Show Cost Breakdown"),
			fieldtype: "Check",
		},
	],

	tree: true,
	initial_depth: 1,

	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;

		if (column.fieldname === "product" && (data.indent === 0 || data._unassigned)) {
			formatted = `<b>${formatted}</b>`;
		}
		if (column.fieldname === "flags" && value) {
			formatted = `<span class="text-warning">${formatted}</span>`;
		}
		if (["contribution_margin", "net_margin", "cm_pct", "nm_pct"].includes(column.fieldname) && value < 0) {
			formatted = `<span class="text-danger">${formatted}</span>`;
		}

		const target = pca_drill_target(column.fieldname);
		if (target && value && data._drill && data._drill[target]) {
			const payload = encodeURIComponent(JSON.stringify({ target, filters: data._drill[target] }));
			formatted = `<a class="pca-drill" data-drill="${payload}" title="${__("Open {0}", [
				__(PCA_DRILL_TARGETS[target]),
			])}">${formatted}</a>`;
		}
		return formatted;
	},

	onload(report) {
		report.page.wrapper.on("click", "a.pca-drill", function (e) {
			e.preventDefault();
			e.stopPropagation();
			const { target, filters } = JSON.parse(decodeURIComponent($(this).attr("data-drill")));
			frappe.route_options = filters;
			frappe.set_route("query-report", PCA_DRILL_TARGETS[target]);
		});
	},
};

// One filter per accounting dimension (e.g. Farm), after Project.
erpnext.utils.add_dimensions(PCA_REPORT, 9);

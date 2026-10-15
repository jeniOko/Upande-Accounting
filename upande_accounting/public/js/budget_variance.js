// Shared by "Budget Variance Report" (ERPNext) and "Budget Variance (Comprehensive)".
frappe.provide("upande_accounting.budget_variance");

// Prefixes "+" to the variance of an income row that is over budget (e.g. KES +200.00).
upande_accounting.budget_variance.mark_income_over_budget = function (value, column, data) {
	if (!data?.is_income || !column.fieldname.includes("variance") || !(data[column.fieldname] > 0)) {
		return value;
	}
	// Insert before the first digit of the displayed text, skipping any HTML tags around it.
	return value.replace(/(^|>)([^<>\d]*)(\d)/, "$1$2+$3");
};

// A toolbar button on each report that opens the other, carrying the current filters across.

upande_accounting.budget_variance.SIMPLE = "Budget Variance Report";
upande_accounting.budget_variance.COMPREHENSIVE = "Budget Variance (Comprehensive)";

upande_accounting.budget_variance.add_switch_button = function (report) {
	const { SIMPLE, COMPREHENSIVE } = upande_accounting.budget_variance;
	const to_comprehensive = report.report_name === SIMPLE;

	report.page.add_inner_button(
		to_comprehensive ? __("Comprehensive Report") : __("Simple Report"),
		() => {
			const values = report.get_filter_values();
			const route_options = {};
			for (const fieldname of [
				"company",
				"from_fiscal_year",
				"to_fiscal_year",
				"period",
				"show_cumulative",
			]) {
				if (values[fieldname] != null) route_options[fieldname] = values[fieldname];
			}

			if (to_comprehensive) {
				// Comprehensive filters are named after the dimension, e.g. cost_center, project.
				if (values.budget_against && values.budget_against_filter?.length) {
					route_options[frappe.scrub(values.budget_against)] = values.budget_against_filter;
				}
			} else {
				// The simple report filters on one dimension; take the first one selected.
				const dimension_filter = report.filters.find(
					(f) =>
						f.df.fieldtype === "MultiSelectList" &&
						f.df.fieldname !== "account" &&
						values[f.df.fieldname]?.length
				);
				if (dimension_filter) {
					route_options.budget_against = dimension_filter.df.options;
					route_options.budget_against_filter = values[dimension_filter.df.fieldname];
				}
			}

			frappe.route_options = route_options;
			frappe.set_route("query-report", to_comprehensive ? COMPREHENSIVE : SIMPLE);
		}
	);
};

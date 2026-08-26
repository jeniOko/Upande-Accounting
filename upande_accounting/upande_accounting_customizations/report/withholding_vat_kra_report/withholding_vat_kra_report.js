// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

/**
 * Withholding VAT KRA Report
 * ===========================
 * KRA Withholding VAT filing report.
 * Only paid WHT VAT records are shown (payment_status = Paid on WTM).
 * Download as XLSX or CSV via the action buttons.
 */

frappe.query_reports["Withholding VAT KRA Report"] = {

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
			label: __("Payment Date From"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("Payment Date To"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "withholding_account",
			label: __("Withholding Account"),
			fieldtype: "Link",
			options: "Account",
			get_query: function () {
				const company = frappe.query_report.get_filter_value("company");
				const filters = {
					account_type: "Tax",
					is_tax_report_account: 1,
					tax_report_type: "Withholding VAT",
				};
				if (company) filters["company"] = company;
				return { filters };
			},
		},
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
		},
	],

	onload: function (report) {
		report.page
			.add_inner_button(__("Download XLSX"), function () {
				download_whvat_report(report, "xlsx");
			})
			.addClass("btn-primary");

		report.page.add_inner_button(__("Download CSV"), function () {
			download_whvat_report(report, "csv");
		});
	},

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;

		if (column.fieldname === "tax_id" && !data.tax_id) {
			value = `<span style="color:#c0392b;">PIN Missing</span>`;
		}

		return value;
	},
};


// ---------------------------------------------------------------------------
// Download handler — builds the file client-side from report.data
// ---------------------------------------------------------------------------

function download_whvat_report(report, format) {
	if (!report.data || !report.data.length) {
		frappe.msgprint({
			title: __("No Data"),
			message: __("Run the report first before downloading."),
			indicator: "orange",
		});
		return;
	}

	if (format === "xlsx") {
		const filters = frappe.query_report.get_filter_values(true);
		const args = { filters: JSON.stringify(filters) };
		window.open(
			frappe.urllib.get_full_url(
				"/api/method/upande_accounting.upande_accounting_customizations.report.withholding_vat_kra_report.withholding_vat_kra_report.download_xlsx?" +
					$.param(args)
			)
		);
		return;
	}

	// Column set stops at Taxable Amount — matches the server-side download_xlsx.
	const headers = ["PIN", "Supplier Name", "Invoice Number", "Invoice Date", "Taxable Amount (KES)"];
	const field_map = ["tax_id", "supplier_name", "bill_no", "bill_date", "taxable_amount"];

	const rows = report.data.map((row) =>
		field_map.map((f) => {
			const val = row[f];
			if (val === null || val === undefined) return "";
			return val;
		})
	);

	const from_date = frappe.query_report.get_filter_value("from_date") || "";
	const to_date = frappe.query_report.get_filter_value("to_date") || "";
	const filename = `Withholding_VAT_KRA_${from_date}_to_${to_date}`;

	download_csv(headers, rows, filename);
}


function download_csv(headers, rows, filename) {
	const escape = (val) => {
		const s = String(val);
		return s.includes(",") || s.includes('"') || s.includes("\n")
			? '"' + s.replace(/"/g, '""') + '"'
			: s;
	};

	const lines = [headers.map(escape).join(",")];
	rows.forEach((row) => lines.push(row.map(escape).join(",")));

	const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
	trigger_download(blob, filename + ".csv");
}


function trigger_download(blob, filename) {
	const url = URL.createObjectURL(blob);
	const link = document.createElement("a");
	link.href = url;
	link.download = filename;
	document.body.appendChild(link);
	link.click();
	document.body.removeChild(link);
	URL.revokeObjectURL(url);
}

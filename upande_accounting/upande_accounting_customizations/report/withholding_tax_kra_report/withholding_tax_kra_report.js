// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

/**
 * Withholding Tax KRA Report
 * ===========================
 * KRA-compatible withholding tax filing report.
 * Columns match the KRA upload format exactly.
 * Download as XLSX or CSV via action buttons.
 */

frappe.query_reports["Withholding Tax KRA Report"] = {

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
            default: frappe.datetime.month_start(),
            reqd: 1,
            description: __("Filters by withholding payment date; unpaid invoices fall back to their own posting date."),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.month_end(),
            reqd: 1,
            description: __("Filters by withholding payment date; unpaid invoices fall back to their own posting date."),
        },
        {
            fieldname: "paid_only",
            label: __("Paid Invoices Only"),
            fieldtype: "Check",
            default: 1,
        },
        {
            fieldname: "withholding_account",
            label: __("Withholding Account"),
            fieldtype: "Link",
            options: "Account",
            get_query: function () {
                const company = frappe.query_report.get_filter_value("company");
                const filters = { account_type: "Tax", is_tax_report_account: 1, tax_report_type: "Withholding Tax" };
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
        // Download as XLSX
        report.page.add_inner_button(__("Download XLSX"), function () {
            download_kra_report(report, "xlsx");
        }).addClass("btn-primary");

        // Download as CSV
        report.page.add_inner_button(__("Download CSV"), function () {
            download_kra_report(report, "csv");
        });
    },

    // ------------------------------------------------------------------
    // Row formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        if (column.fieldname === "residential_status") {
            if (data.residential_status === "Non Resident") {
                value = `<span style="color:#c0392b; font-weight:600;">Non Resident</span>`;
            } else {
                value = `<span style="color:#27ae60;">Resident</span>`;
            }
        }

        return value;
    },
};


// ---------------------------------------------------------------------------
// Download handler
// Builds the file client-side from report.data so no extra server call needed.
// ---------------------------------------------------------------------------

function download_kra_report(report, format) {
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
                "/api/method/upande_accounting.upande_accounting_customizations.report.withholding_tax_kra_report.withholding_tax_kra_report.download_xlsx?" +
                    $.param(args)
            )
        );
        return;
    }

    const headers = [
        "Nature of Transaction",
        "Country",
        "Residential Status",
        "Date of Payment",
        "PIN",
        "Supplier Name",
        "Invoice Number",
        "Email Address",
        "Gross Amount",
        "Rate",
        "Tax Amount",
    ];

    const field_map = [
        "nature_of_transaction",
        "country",
        "residential_status",
        "payment_date",
        "tax_id",
        "supplier_name",
        "bill_no",
        "email",
        "gross_amount",
        "tax_rate",
        "tax_amount",
    ];

    const rows = report.data.map(row =>
        field_map.map(f => {
            const val = row[f];
            if (val === null || val === undefined) return "";
            return val;
        })
    );

    const from_date = frappe.query_report.get_filter_value("from_date") || "";
    const to_date   = frappe.query_report.get_filter_value("to_date")   || "";
    const filename  = `Withholding_Tax_KRA_${from_date}_to_${to_date}`;

    download_csv(headers, rows, filename);
}


function download_csv(headers, rows, filename) {
    const escape = val => {
        const s = String(val);
        return s.includes(",") || s.includes('"') || s.includes("\n")
            ? '"' + s.replace(/"/g, '""') + '"'
            : s;
    };

    const lines = [headers.map(escape).join(",")];
    rows.forEach(row => lines.push(row.map(escape).join(",")));

    const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    trigger_download(blob, filename + ".csv");
}


function trigger_download(blob, filename) {
    const url  = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href  = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}
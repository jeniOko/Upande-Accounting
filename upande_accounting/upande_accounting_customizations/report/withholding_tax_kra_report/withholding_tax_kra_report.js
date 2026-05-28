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
            // WHTAX or WHVAT — required, determines which accounts to query
            fieldname: "withholding_type",
            label: __("Withholding Type"),
            fieldtype: "Select",
            options: "\nWHTAX\nWHVAT",
            reqd: 1,
            on_change: function () {
                // Reset account filter when type changes
                frappe.query_report.set_filter_value("withholding_account", "");
            },
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
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
                const wh_type = frappe.query_report.get_filter_value("withholding_type");
                const filters = { account_type: "Tax", is_tax_report_account: 1 };
                if (wh_type) filters["tax_report_type"] = wh_type;
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

    const wh_type   = frappe.query_report.get_filter_value("withholding_type") || "WHTAX";
    const from_date = frappe.query_report.get_filter_value("from_date") || "";
    const to_date   = frappe.query_report.get_filter_value("to_date")   || "";
    const filename  = `${wh_type}_KRA_${from_date}_to_${to_date}`;

    if (format === "csv") {
        download_csv(headers, rows, filename);
    } else {
        download_xlsx(headers, rows, filename);
    }
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


function download_xlsx(headers, rows, filename) {
    // Use SheetJS (xlsx) which is available in Frappe's frontend bundle
    // If not available, fall back to CSV with a notice.
    if (typeof XLSX === "undefined") {
        frappe.msgprint({
            title: __("XLSX Not Available"),
            message: __("SheetJS library not found. Downloading as CSV instead."),
            indicator: "orange",
        });
        download_csv(headers, rows, filename);
        return;
    }

    const ws_data = [headers, ...rows];
    const ws      = XLSX.utils.aoa_to_sheet(ws_data);

    // Column widths
    ws["!cols"] = [
        { wch: 45 }, // Nature of Transaction
        { wch: 15 }, // Country
        { wch: 16 }, // Residential Status
        { wch: 14 }, // Date of Payment
        { wch: 16 }, // PIN
        { wch: 30 }, // Supplier Name
        { wch: 20 }, // Invoice Number
        { wch: 25 }, // Email
        { wch: 16 }, // Gross Amount
        { wch: 8  }, // Rate
        { wch: 16 }, // Tax Amount
    ];

    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Withholding Tax");

    const wbout = XLSX.write(wb, { bookType: "xlsx", type: "array" });
    const blob  = new Blob([wbout], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });
    trigger_download(blob, filename + ".xlsx");
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
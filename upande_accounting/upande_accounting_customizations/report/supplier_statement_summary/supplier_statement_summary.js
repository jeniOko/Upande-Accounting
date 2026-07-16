// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Supplier Statement Summary"] = {

    onload: function (report) {
        // Top-right button: open detail report (no supplier pre-selected)
        report.page.add_inner_button(__("Supplier Statement"), function () {
            frappe.route_options = {
                company:       frappe.query_report.get_filter_value("company"),
                from_date:     frappe.query_report.get_filter_value("from_date"),
                to_date:       frappe.query_report.get_filter_value("to_date"),
                include_draft: frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Supplier Statement Of Account");
        });

        // Use document-level delegation — Frappe's DataTable re-renders cells
        // inside a virtual scroll container that doesn't bubble through report.wrapper
        // reliably. $(document) is the only selector that always works.
        $(document).off("click.supp-nav").on("click.supp-nav", "[data-supplier-nav]", function () {
            const supplier = $(this).attr("data-supplier-nav");
            if (!supplier) return;
            frappe.route_options = {
                supplier:      supplier,
                company:       frappe.query_report.get_filter_value("company"),
                from_date:     frappe.query_report.get_filter_value("from_date"),
                to_date:       frappe.query_report.get_filter_value("to_date"),
                include_draft: frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Supplier Statement Of Account");
        });
    },

    // ------------------------------------------------------------------
    // Filters
    // ------------------------------------------------------------------
    filters: [
        {
            fieldname: "company",
            label:     __("Company"),
            fieldtype: "Link",
            options:   "Company",
            default:   frappe.defaults.get_user_default("Company"),
            reqd:      1,
        },
        {
            fieldname: "from_date",
            label:     __("From Date"),
            fieldtype: "Date",
            default:   frappe.datetime.add_months(frappe.datetime.get_today(), -3),
            reqd:      1,
        },
        {
            fieldname: "to_date",
            label:     __("To Date"),
            fieldtype: "Date",
            default:   frappe.datetime.get_today(),
            reqd:      1,
        },
        {
            fieldname: "show_in_company_currency",
            label:     __("Show in Company Currency"),
            fieldtype: "Check",
            default:   0,
        },
        {
            fieldname: "include_draft",
            label:     __("Include Draft Bills"),
            fieldtype: "Check",
            default:   0,
        },
    ],

    // ------------------------------------------------------------------
    // Formatter
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        if (!data) return default_formatter(value, row, column, data);

        // Supplier column → clickable link to detail report
        if (column.fieldname === "supplier" && data.supplier) {
            const escaped = (data.supplier || "").replace(/"/g, "&quot;");
            const display = data.supplier_name || data.supplier;
            return `<a data-supplier-nav="${escaped}"
                       style="color:var(--primary); cursor:pointer; text-decoration:underline;"
                       title="${__("View Statement")}">${display}</a>`;
        }

        value = default_formatter(value, row, column, data);

        // Closing balance: negative = debit balance (overpaid supplier), shown in red
        if (column.fieldname === "closing_balance" && flt(data.closing_balance) < 0) {
            value = `<span style="color:#c0392b;">${value}</span>`;
        }

        return value;
    },

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },
};

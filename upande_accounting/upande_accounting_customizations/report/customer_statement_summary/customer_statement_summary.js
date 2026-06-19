// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Customer Statement Summary"] = {

    onload: function (report) {
        // Top-right button: open detail report (no customer pre-selected)
        report.page.add_inner_button(__("Customer Statement"), function () {
            frappe.route_options = {
                company:       frappe.query_report.get_filter_value("company"),
                from_date:     frappe.query_report.get_filter_value("from_date"),
                to_date:       frappe.query_report.get_filter_value("to_date"),
                include_draft: frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Customer Statement Of Account");
        });

        // Use document-level delegation — Frappe's DataTable re-renders cells
        // inside a virtual scroll container that doesn't bubble through report.wrapper
        // reliably. $(document) is the only selector that always works.
        $(document).off("click.cust-nav").on("click.cust-nav", "[data-customer-nav]", function () {
            const customer = $(this).attr("data-customer-nav");
            if (!customer) return;
            frappe.route_options = {
                customer:      customer,
                company:       frappe.query_report.get_filter_value("company"),
                from_date:     frappe.query_report.get_filter_value("from_date"),
                to_date:       frappe.query_report.get_filter_value("to_date"),
                include_draft: frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Customer Statement Of Account");
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
            label:     __("Include Draft Invoices"),
            fieldtype: "Check",
            default:   0,
        },
    ],

    // ------------------------------------------------------------------
    // Formatter
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        if (!data) return default_formatter(value, row, column, data);

        // Customer column → clickable link to detail report
        if (column.fieldname === "customer" && data.customer) {
            const escaped = (data.customer || "").replace(/"/g, "&quot;");
            const display = data.customer_name || data.customer;
            return `<a data-customer-nav="${escaped}"
                       style="color:var(--primary); cursor:pointer; text-decoration:underline;"
                       title="${__("View Statement")}">${display}</a>`;
        }

        value = default_formatter(value, row, column, data);

        // Closing balance: negative = credit balance (overpaid), shown in red
        if (column.fieldname === "closing_balance" && flt(data.closing_balance) < 0) {
            value = `<span style="color:#c0392b;">${value}</span>`;
        }

        return value;
    },

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },
};

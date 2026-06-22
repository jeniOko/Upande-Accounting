// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Debtors Aging Summary"] = {

    // ------------------------------------------------------------------
    // On load
    // ------------------------------------------------------------------
    onload: function (report) {
        // Switch to the per-invoice detail view, carrying all filters across
        report.page.add_inner_button(__("Debtors Aging (Detailed View)"), function () {
            frappe.route_options = {
                company:           frappe.query_report.get_filter_value("company"),
                report_date:       frappe.query_report.get_filter_value("report_date"),
                ageing_based_on:   frappe.query_report.get_filter_value("ageing_based_on"),
                range:             frappe.query_report.get_filter_value("range"),
                party:             frappe.query_report.get_filter_value("party"),
                in_party_currency: frappe.query_report.get_filter_value("in_party_currency") || 0,
                include_draft:     frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Debtors Aging");
        });

        // Navigate to the Customer Statement Of Account
        report.page.add_inner_button(__("Customer Statement"), function () {
            frappe.route_options = {
                company:       frappe.query_report.get_filter_value("company"),
                to_date:       frappe.query_report.get_filter_value("report_date"),
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
            reqd:      1,
            default:   frappe.defaults.get_user_default("Company"),
        },
        {
            fieldname: "report_date",
            label:     __("As On Date"),
            fieldtype: "Date",
            reqd:      1,
            default:   frappe.datetime.get_today(),
        },
        {
            fieldname: "ageing_based_on",
            label:     __("Ageing Based On"),
            fieldtype: "Select",
            options:   "Posting Date\nDue Date",
            default:   "Due Date",
        },
        {
            fieldname: "range",
            label:     __("Ageing Range (days)"),
            fieldtype: "Data",
            default:   "30, 60, 90, 120",
        },
        {
            fieldname: "party",
            label:     __("Customer(s)"),
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                return frappe.db.get_link_options("Customer", txt);
            },
        },
        {
            fieldname: "customer_group",
            label:     __("Customer Group"),
            fieldtype: "Link",
            options:   "Customer Group",
        },
        {
            fieldname: "territory",
            label:     __("Territory"),
            fieldtype: "Link",
            options:   "Territory",
        },
        {
            fieldname: "payment_terms_template",
            label:     __("Payment Terms Template"),
            fieldtype: "Link",
            options:   "Payment Terms Template",
        },
        {
            fieldname: "sales_person",
            label:     __("Sales Person"),
            fieldtype: "Link",
            options:   "Sales Person",
        },
        {
            fieldname: "finance_book",
            label:     __("Finance Book"),
            fieldtype: "Link",
            options:   "Finance Book",
        },
        {
            fieldname: "in_party_currency",
            label:     __("In Party Currency"),
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

        value = default_formatter(value, row, column, data);

        // Bold sub-total rows (group_by_party)
        if (data.bold) {
            value = `<strong>${value}</strong>`;
        }

        // Party column: append a small "(+draft)" badge when the row
        // contains draft invoice amounts so the user knows the total is
        // not fully posted yet.
        if (column.fieldname === "party" && data.has_draft) {
            value = `${value} <span style="
                font-size:0.75em;
                color:#fff;
                background:#e67e22;
                border-radius:3px;
                padding:1px 4px;
                vertical-align:middle;
                font-style:italic;">${__("Has Drafts")}</span>`;
        }

        // Highlight outstanding amount: red when positive
        if (column.fieldname === "outstanding" && flt(data.outstanding) > 0) {
            value = `<span style="color:#e74c3c; font-weight:500;">${value}</span>`;
        }

        return value;
    },

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },
};

// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Creditors Aging"] = {

    // ------------------------------------------------------------------
    // On load
    // ------------------------------------------------------------------
    onload: function (report) {
        report.page.add_inner_button(__("Creditors Aging Summary"), function () {
            frappe.route_options = {
                company:           frappe.query_report.get_filter_value("company"),
                report_date:       frappe.query_report.get_filter_value("report_date"),
                ageing_based_on:   frappe.query_report.get_filter_value("ageing_based_on"),
                range:             frappe.query_report.get_filter_value("range"),
                party:             frappe.query_report.get_filter_value("party"),
                in_party_currency: frappe.query_report.get_filter_value("in_party_currency") || 0,
                include_draft:     frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Creditors Aging Summary");
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
            fieldname: "party_account",
            label:     __("Payable Account"),
            fieldtype: "Link",
            options:   "Account",
            get_query: () => {
                const company = frappe.query_report.get_filter_value("company");
                return { filters: { company, account_type: "Payable", is_group: 0 } };
            },
        },
        {
            fieldname: "party",
            label:     __("Supplier(s)"),
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                return frappe.db.get_link_options("Supplier", txt);
            },
        },
        {
            fieldname: "supplier_group",
            label:     __("Supplier Group"),
            fieldtype: "Link",
            options:   "Supplier Group",
        },
        {
            fieldname: "payment_terms_template",
            label:     __("Payment Terms Template"),
            fieldtype: "Link",
            options:   "Payment Terms Template",
        },
        {
            fieldname: "finance_book",
            label:     __("Finance Book"),
            fieldtype: "Link",
            options:   "Finance Book",
        },
        {
            fieldname: "cost_center",
            label:     __("Cost Center"),
            fieldtype: "Link",
            options:   "Cost Center",
            get_query: () => {
                const company = frappe.query_report.get_filter_value("company");
                return { filters: { company } };
            },
        },
        {
            fieldname: "based_on_payment_terms",
            label:     __("Based On Payment Terms"),
            fieldtype: "Check",
        },
        {
            fieldname: "show_future_payments",
            label:     __("Show Future Payments"),
            fieldtype: "Check",
        },
        {
            fieldname: "show_remarks",
            label:     __("Show Remarks"),
            fieldtype: "Check",
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

        if (data.bold) {
            value = `<strong>${value}</strong>`;
        }

        // Status column — colour-coded by urgency
        if (column.fieldname === "status" && data.status && !data.is_draft) {
            const palette = {
                "Overdue":    { color: "#c0392b", bg: "#fdecea" },
                "Almost Due": { color: "#d35400", bg: "#fef3e2" },
                "Not Due":    { color: "#1e8449", bg: "#eafaf1" },
            };
            const style = palette[data.status];
            if (style) {
                value = `<span style="
                    color:${style.color};
                    background:${style.bg};
                    border-radius:3px;
                    padding:1px 6px;
                    font-weight:500;
                    font-size:0.85em;">${value}</span>`;
            }
        }

        // Draft invoice rows — orange italic across all cells
        if (data.is_draft) {
            value = `<span style="color:#e67e22; font-style:italic;">${value || ""}</span>`;
        }

        return value;
    },

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },
};

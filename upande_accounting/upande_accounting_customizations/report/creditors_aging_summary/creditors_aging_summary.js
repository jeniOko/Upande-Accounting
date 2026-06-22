// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Creditors Aging Summary"] = {

    // ------------------------------------------------------------------
    // On load
    // ------------------------------------------------------------------
    onload: function (report) {
        report.page.add_inner_button(__("Creditors Aging"), function () {
            frappe.route_options = {
                company:           frappe.query_report.get_filter_value("company"),
                report_date:       frappe.query_report.get_filter_value("report_date"),
                ageing_based_on:   frappe.query_report.get_filter_value("ageing_based_on"),
                range:             frappe.query_report.get_filter_value("range"),
                party:             frappe.query_report.get_filter_value("party"),
                in_party_currency: frappe.query_report.get_filter_value("in_party_currency") || 0,
                include_draft:     frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Creditors Aging");
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

        // Party column — "Has Drafts" badge
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

        // Outstanding amount — red when positive
        if (column.fieldname === "outstanding" && flt(data.outstanding) > 0) {
            value = `<span style="color:#e74c3c; font-weight:500;">${value}</span>`;
        }

        return value;
    },

    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },
};

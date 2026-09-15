// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["Customer Statement Of Account"] = {

    onload: function (report) {
        // Top-right button: back to summary with current dates
        report.page.add_inner_button(__("Statement Summary"), function () {
            frappe.route_options = {
                company:       frappe.query_report.get_filter_value("company"),
                from_date:     frappe.query_report.get_filter_value("from_date"),
                to_date:       frappe.query_report.get_filter_value("to_date"),
                include_draft: frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Customer Statement Summary");
        });

        // The built-in "Print" button in the report menu renders html_format
        // client-side with a template engine that can't parse this report's
        // Jinja template, so it fails silently. This button instead asks the
        // server to render the same template properly and returns a PDF.
        report.page.add_inner_button(__("Print Statement"), function () {
            const customer  = frappe.query_report.get_filter_value("customer");
            const company   = frappe.query_report.get_filter_value("company");
            const from_date = frappe.query_report.get_filter_value("from_date");
            const to_date   = frappe.query_report.get_filter_value("to_date");

            if (!customer || !company || !from_date || !to_date) {
                frappe.msgprint(__("Please set Company, Customer, From Date and To Date."));
                return;
            }

            const params = new URLSearchParams({
                customer:      customer,
                company:       company,
                from_date:     from_date,
                to_date:       to_date,
                include_draft: frappe.query_report.get_filter_value("include_draft") || 0,
            });

            window.open(
                "/api/method/upande_accounting.upande_accounting_customizations.report."
                + "customer_statement_of_account.customer_statement_of_account.download_statement_pdf?"
                + params.toString()
            );
        });
    },

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
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "Link",
            options: "Customer",
            reqd: 1,
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "include_draft",
            label: __("Include Draft Invoices"),
            fieldtype: "Check",
            default: 0,
        },
    ],

    // ------------------------------------------------------------------
    // Row formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        // Opening / closing balance — bold all cells
        if (data.is_opening || data.is_closing) {
            value = `<strong>${value || ""}</strong>`;
        }

        // Document type labels (normal invoice rows)
        if (column.fieldname === "display_type" && !data.is_opening && !data.is_closing) {
            if (data.display_type === "Credit Note") {
                value = `<span style="font-weight:300;">Credit Note</span>`;
            } else if (data.display_type === "Receipt") {
                value = `<span style="font-weight:300;">Receipt</span>`;
            } else if (data.display_type === "Invoice") {
                value = `<span style="font-weight:300;">Invoice</span>`;
            }
        }

        // Balance colour on invoice rows — uses ageing_level computed in Python
        // against to_date, so "overdue" reflects the report date, not today.
        if (
            column.fieldname === "balance" &&
            data.voucher_type === "Sales Invoice" &&
            flt(data.balance) > 0 &&
            data.ageing_level !== null && data.ageing_level !== undefined
        ) {
            // 5 distinct hue families: green → blue → amber → purple → red (critical)
            const colours = ["#27ae60", "#2980b9", "#f39c12", "#8e44ad", "#e74c3c"];
            const colour  = colours[Math.min(data.ageing_level, colours.length - 1)];
            value = `<span style="color:${colour}; font-weight:500;">${value}</span>`;
        }

        // Draft invoice rows — orange italic
        if (data.is_draft) {
            value = `<span style="color:#e67e22; font-style:italic;">${value || ""}</span>`;
        }

        return value;
    },

    // ------------------------------------------------------------------
    // Checkbox + row highlight
    // ------------------------------------------------------------------
    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },

    // ------------------------------------------------------------------
    // After render: attach checkbox row-highlight listener
    // ------------------------------------------------------------------
    after_datatable_render: function (datatable) {

        const HIGHLIGHT_BG     = "#fff9c4";
        const HIGHLIGHT_BORDER = "2px solid #f5a623";

        const wrapper = (datatable.wrapper)
            || (datatable.$el && datatable.$el[0])
            || (datatable.bodyScrollable && datatable.bodyScrollable.closest(".datatable"));

        if (!wrapper || wrapper.__highlightListenerAttached) return;
        wrapper.__highlightListenerAttached = true;

        wrapper.addEventListener("click", function (e) {
            const checkbox = e.target.closest("input[type='checkbox']");
            if (!checkbox) return;
            const tr = checkbox.closest("tr");
            if (!tr || tr.closest("thead")) return;
            const isChecked = checkbox.checked;
            tr.querySelectorAll("td").forEach(td => {
                if (isChecked) {
                    td.style.backgroundColor = HIGHLIGHT_BG;
                    td.style.borderTop       = HIGHLIGHT_BORDER;
                    td.style.borderBottom    = HIGHLIGHT_BORDER;
                    td.style.transition      = "background-color 0.15s ease";
                } else {
                    td.style.backgroundColor = "";
                    td.style.borderTop       = "";
                    td.style.borderBottom    = "";
                }
            });
        });
    },
};
// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.query_reports["VAT3 Filing Summary"] = {

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
            // Default to first day of current month
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
            // Dynamically lists only accounts tagged as VAT3 report accounts.
            // If multiple VAT3 accounts exist the report shows all; selecting
            // one here restricts to that account's rows only.
            fieldname: "vat_account",
            label: __("VAT Account"),
            fieldtype: "Link",
            options: "Account",
            get_query: function () {
                const company = frappe.query_report.get_filter_value("company");
                return {
                    filters: {
                        account_type:          "Tax",
                        is_tax_report_account: 1,
                        tax_report_type:       "VAT3",
                        company:               company || undefined,
                    },
                };
            },
        },
        {
            fieldname: "supplier",
            label: __("Supplier"),
            fieldtype: "Link",
            options: "Supplier",
        },
        {
            fieldname: "type_of_purchase",
            label: __("Type of Purchase"),
            fieldtype: "Select",
            options: "\nLocal\nImport",
        },
    ],

    // ------------------------------------------------------------------
    // Column formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (!data) return value;

        // Colour-code purchase type
        if (column.fieldname === "type_of_purchase") {
            if (data.type_of_purchase === "Local") {
                value = `<span style="color:#1a6ebd; font-weight:600;">Local</span>`;
            } else if (data.type_of_purchase === "Import") {
                value = `<span style="color:#e67e22; font-weight:600;">Import</span>`;
            }
        }

        // Flag rows with zero VAT — may need review
        if (column.fieldname === "vat_amount" && flt(data.vat_amount) === 0) {
            value = `<span style="color:#c0392b; font-weight:600;">0.00</span>`;
        }

        return value;
    },

    // ------------------------------------------------------------------
    // Checkbox + row highlight
    // ------------------------------------------------------------------
    get_datatable_options(options) {
        return Object.assign(options, {
            checkboxColumn: true,
        });
    },

    after_datatable_render: function (datatable) {
        const HIGHLIGHT_BG     = "#fff9c4";
        const HIGHLIGHT_BORDER = "2px solid #f5a623";

        const wrapper = (datatable.wrapper)
            || (datatable.$el && datatable.$el[0])
            || (datatable.bodyScrollable && datatable.bodyScrollable.closest(".datatable"));

        if (!wrapper) return;
        if (wrapper.__highlightListenerAttached) return;
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
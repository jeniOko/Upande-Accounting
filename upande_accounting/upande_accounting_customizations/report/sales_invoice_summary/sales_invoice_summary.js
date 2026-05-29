// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

/**
 * Dynamic columns: auto-detects tax and charge account heads in the period.
 * User can further filter via the "Show Columns" multiselect.
 *
 * The show_columns filter is re-populated whenever company/date/customer
 * filters change, so it always reflects what's actually in the data.
 */

frappe.query_reports["Sales Invoice Summary"] = {

    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1,
            on_change: () => refresh_column_options(),
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1,
            on_change: () => refresh_column_options(),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
            on_change: () => refresh_column_options(),
        },
        {
            fieldname: "customer",
            label: __("Customer"),
            fieldtype: "Link",
            options: "Customer",
            on_change: () => refresh_column_options(),
        },
        {
            // Multiselect of account heads.
            // Options are populated dynamically via refresh_column_options().
            // Leaving blank = show all discovered columns.
            fieldname: "show_columns",
            label: __("Show Columns"),
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                // Called by the MultiSelectList widget to get options.
                // Returns already-fetched options stored on the filter object.
                const f = frappe.query_report.get_filter("show_columns");
                return (f && f._column_options) ? f._column_options : [];
            },
        },
    ],

    // ------------------------------------------------------------------
    // Called once when the report page first loads
    // ------------------------------------------------------------------
    onload: function (report) {
        refresh_column_options();
    },

    // ------------------------------------------------------------------
    // Row formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        // Separator row — render nothing
        if (data.is_separator) {
            return "";
        }

        // Grand total row — bold + note that values are in company currency
        if (data.is_grand_total) {
            if (column.fieldname === "customer") {
                return `<strong style="color:#111; f">${value || ""}</strong>`;
            }
            return `<strong style="color:#111;">${value || ""}</strong>`;
        }

        // Subtotal row — "Total for Customer Name", bold + light underline
        if (data.is_subtotal) {
            return `<strong style="border-top:1px solid #ccc; display:block; padding-top:3px;">${value || ""}</strong>`;
        }

        // Credit notes — muted
        if (data.is_return) {
            return `<span style="color:#888; font-style:italic;">${value || ""}</span>`;
        }

        return value;
    },

    // ------------------------------------------------------------------
    // Checkbox + row highlight
    // ------------------------------------------------------------------
    get_datatable_options(options) {
        return Object.assign(options, { checkboxColumn: true });
    },

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


// ---------------------------------------------------------------------------
// refresh_column_options
// Fetches available account heads from the server based on current
// date/company/customer filters and stores them on the filter object so
// the MultiSelectList widget can render them.
// ---------------------------------------------------------------------------
function refresh_column_options() {
    // Debounce — wait 400ms after last change before hitting server
    if (refresh_column_options._timer) clearTimeout(refresh_column_options._timer);
    refresh_column_options._timer = setTimeout(() => {

        const get = (f) => frappe.query_report.get_filter_value(f);
        const company   = get("company");
        const from_date = get("from_date");
        const to_date   = get("to_date");
        const customer  = get("customer");

        if (!company || !from_date || !to_date) return;

        frappe.call({
            method: "upande_accounting.upande_accounting_customizations.report"
                  + ".sales_invoice_summary.sales_invoice_summary"
                  + ".get_dynamic_columns_for_filter",
            args: { company, from_date, to_date, customer: customer || null },
            callback: function (r) {
                if (!r.message) return;

                // Build option list for MultiSelectList
                // Group label is shown as a separator in the dropdown
                const options = r.message.map(item => ({
                    value: item.value,
                    description: item.group,   // shown as subtitle in the pill
                    label: item.label,
                }));

                // Store on the filter object so get_data() can return them
                const f = frappe.query_report.get_filter("show_columns");
                if (f) {
                    f._column_options = options;
                    // Refresh the widget display
                    f.refresh && f.refresh();
                }
            },
        });
    }, 400);
}
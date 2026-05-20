// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

/**

 *
 * Dynamic tax columns: auto-detected from Purchase Taxes and Charges.
 * Non-tax charges are always collapsed into a single "Additional Charges" column.
 * User can filter tax columns via the Show Columns multiselect.
 */
frappe.query_reports["Purchase Invoice Summary"] = {

    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1,
            on_change: () => refresh_purchase_column_options(),
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1,
            on_change: () => refresh_purchase_column_options(),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
            on_change: () => refresh_purchase_column_options(),
        },
        {
            fieldname: "supplier",
            label: __("Supplier"),
            fieldtype: "Link",
            options: "Supplier",
            on_change: () => refresh_purchase_column_options(),
        },
        {
            // Lists only TAX account heads — non-tax charges are always collapsed.
            // Leaving blank = show all discovered tax columns.
            fieldname: "show_columns",
            label: __("Show Tax Columns"),
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                const f = frappe.query_report.get_filter("show_columns");
                return (f && f._column_options) ? f._column_options : [];
            },
        },
        {
            // When checked: hides all tax columns and additional charges column.
            // Shows only Net Amount, Grand Total, and company currency totals.
            fieldname: "net_only",
            label: __("Net Amounts Only"),
            fieldtype: "Check",
            default: 0,
            on_change: function () {
                const netOnly = frappe.query_report.get_filter_value("net_only");
                const f = frappe.query_report.get_filter("show_columns");
                if (f && f.$wrapper) {
                    f.$wrapper.css("opacity", netOnly ? 0.4 : 1);
                }
            },
        },
    ],

    // ------------------------------------------------------------------
    // Called once on page load
    // ------------------------------------------------------------------
    onload: function (report) {
        refresh_purchase_column_options();

        // Fix: Frappe's MultiSelectList dropdown gets buried under the
        // DataTable because the report wrapper creates a new stacking context.
        //
        // Strategy: wait for the filter bar to render, then find the
        // show_columns control and move its awesomplete/dropdown list to
        // document.body so it escapes all stacking contexts entirely.
        // We also set fixed positioning so it tracks the input correctly.

        setTimeout(() => {
            fix_multiselect_zindex();
        }, 800);
    },

    // ------------------------------------------------------------------
    // Row formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        // Separator rows — render nothing
        if (data.is_separator) return "";

        // Grand total row — bold
        if (data.is_grand_total) {
            if (column.fieldname === "supplier") {
                return `<strong style="color:#111; font-size:11px;">${value || ""}</strong>`;
            }
            return `<strong style="color:#111;">${value || ""}</strong>`;
        }

        // Subtotal row — bold with light top border
        if (data.is_subtotal) {
            return `<strong style="border-top:1px solid #ccc; display:block; padding-top:3px;">${value || ""}</strong>`;
        }

        // Debit notes (is_return) — muted italic
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
// refresh_purchase_column_options
// Fetches TAX account heads from the server and loads them into the
// Show Tax Columns multiselect filter.
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// fix_multiselect_zindex
// Moves the awesomplete dropdown for show_columns out of the stacking
// context created by the report wrapper and re-attaches it to document.body
// with fixed positioning so it always renders on top.
// ---------------------------------------------------------------------------
function fix_multiselect_zindex() {
    // Find the filter wrapper for show_columns
    const filterEl = document.querySelector(
        '.frappe-control[data-fieldname="show_columns"]'
    );
    if (!filterEl) return;

    const input = filterEl.querySelector("input.input-with-feedback, input[type='text']");
    if (!input) return;

    // Inject override styles that work regardless of stacking context
    const styleId = "purchase-multiselect-fix";
    if (!document.getElementById(styleId)) {
        const style = document.createElement("style");
        style.id = styleId;
        style.textContent = `
            /* Lift the entire filter bar above the datatable */
            .page-form.flex,
            .standard-filter-section,
            .filter-section {
                position: relative !important;
                z-index: 200 !important;
            }

            /* Force the awesomplete list and any dropdown inside
               show_columns to always appear above everything */
            .frappe-control[data-fieldname="show_columns"] {
                position: relative !important;
                z-index: 9999 !important;
                overflow: visible !important;
            }
            .frappe-control[data-fieldname="show_columns"] ul.awesomplete__list,
            .frappe-control[data-fieldname="show_columns"] .awesomplete > ul,
            .frappe-control[data-fieldname="show_columns"] ul[role="listbox"],
            .frappe-control[data-fieldname="show_columns"] .dropdown-menu,
            .frappe-control[data-fieldname="show_columns"] .multiselect-dropdown {
                z-index: 9999 !important;
                position: absolute !important;
                /* Render dropdown downward from the input */
                top: 100% !important;
                left: 0 !important;
                min-width: 280px !important;
                max-height: 240px !important;
                overflow-y: auto !important;
                background: #fff !important;
                border: 1px solid #d1d8dd !important;
                border-radius: 4px !important;
                box-shadow: 0 4px 16px rgba(0,0,0,0.15) !important;
            }

            /* List items — force readable text and solid background */
            .frappe-control[data-fieldname="show_columns"] ul.awesomplete__list li,
            .frappe-control[data-fieldname="show_columns"] ul[role="listbox"] li,
            .frappe-control[data-fieldname="show_columns"] .dropdown-menu li,
            .frappe-control[data-fieldname="show_columns"] .dropdown-menu a {
                color: #111111 !important;
                background-color: #ffffff !important;
                padding: 8px 12px !important;
                font-size: 13px !important;
                cursor: pointer !important;
                display: block !important;
                opacity: 1 !important;
                line-height: 1.5 !important;
            }

            /* Hover state */
            .frappe-control[data-fieldname="show_columns"] ul.awesomplete__list li:hover,
            .frappe-control[data-fieldname="show_columns"] ul[role="listbox"] li:hover,
            .frappe-control[data-fieldname="show_columns"] .dropdown-menu li:hover,
            .frappe-control[data-fieldname="show_columns"] .dropdown-menu a:hover,
            .frappe-control[data-fieldname="show_columns"] ul li[aria-selected="true"] {
                background-color: #f0f4f8 !important;
                color: #000000 !important;
            }

            /* Small grey label under each item (the "Tax" group label) */
            .frappe-control[data-fieldname="show_columns"] .multiselect-result-item small,
            .frappe-control[data-fieldname="show_columns"] ul li small {
                color: #888888 !important;
                font-size: 11px !important;
                display: block !important;
            }
        `;
        document.head.appendChild(style);
    }

    // Additionally, listen for when the awesomplete list opens and
    // nudge it back into view if the browser has clipped it
    const observer = new MutationObserver(() => {
        const list = filterEl.querySelector(
            "ul.awesomplete__list, ul[role='listbox'], .dropdown-menu"
        );
        if (list && list.children.length > 0) {
            // Make sure the parent chain has no overflow:hidden clipping it
            let el = list.parentElement;
            while (el && el !== document.body) {
                const style = window.getComputedStyle(el);
                if (style.overflow === "hidden" || style.overflowY === "hidden") {
                    el.style.overflow = "visible";
                }
                el = el.parentElement;
            }
        }
    });

    observer.observe(filterEl, { childList: true, subtree: true });
}


function refresh_purchase_column_options() {
    if (refresh_purchase_column_options._timer) {
        clearTimeout(refresh_purchase_column_options._timer);
    }
    refresh_purchase_column_options._timer = setTimeout(() => {

        const get = (f) => frappe.query_report.get_filter_value(f);
        const company   = get("company");
        const from_date = get("from_date");
        const to_date   = get("to_date");
        const supplier  = get("supplier");

        if (!company || !from_date || !to_date) return;

        frappe.call({
            method: "upande_accounting.upande_accounting_customizations.report"
                  + ".purchase_invoice_summary.purchase_invoice_summary"
                  + ".get_dynamic_columns_for_filter",
            args: { company, from_date, to_date, supplier: supplier || null },
            callback: function (r) {
                if (!r.message) return;

                const options = r.message.map(item => ({
                    value:       item.value,
                    label:       item.label,
                    description: "Tax",
                }));

                const f = frappe.query_report.get_filter("show_columns");
                if (f) {
                    f._column_options = options;
                    f.refresh && f.refresh();
                }
            },
        });
    }, 400);
}
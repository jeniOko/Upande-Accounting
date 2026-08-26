// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

function toggleAgeingRows(show) {
    // Find the DataTable body wrapper inside the report area
    const reportWrapper = document.querySelector(
        ".frappe-report .dt-scrollable, " +
        ".frappe-report .datatable .dt-body, " +
        ".report-wrapper .dt-scrollable"
    );
    if (!reportWrapper) return;

    const allRows = Array.from(reportWrapper.querySelectorAll(".dt-row"));
    if (!allRows.length) return;

    // Locate the closing-balance row — it's the last row whose
    // description cell contains "Closing Balance"
    let closingIdx = -1;
    allRows.forEach((tr, idx) => {
        const cells = Array.from(tr.querySelectorAll(".dt-cell"));
        const hasClosing = cells.some(
            c => (c.textContent || "").trim() === "Closing Balance"
        );
        if (hasClosing) closingIdx = idx;
    });

    if (closingIdx === -1) return;   // closing row not found yet — nothing to do

    // Hide/show every row after the closing balance row
    allRows.forEach((tr, idx) => {
        if (idx > closingIdx) {
            tr.style.display = show ? "" : "none";
        }
    });
}


frappe.query_reports["Supplier Statement Of Account"] = {

    onload: function (report) {
        // Top-right button: back to summary with current dates
        report.page.add_inner_button(__("Statement Summary"), function () {
            frappe.route_options = {
                company:       frappe.query_report.get_filter_value("company"),
                from_date:     frappe.query_report.get_filter_value("from_date"),
                to_date:       frappe.query_report.get_filter_value("to_date"),
                include_draft: frappe.query_report.get_filter_value("include_draft") || 0,
            };
            frappe.set_route("query-report", "Supplier Statement Summary");
        });

        // The built-in "Print" button in the report menu renders html_format
        // client-side with a template engine that can't parse this report's
        // Jinja template, so it fails silently. This button instead asks the
        // server to render the same template properly and returns a PDF.
        report.page.add_inner_button(__("Print Statement"), function () {
            const supplier  = frappe.query_report.get_filter_value("supplier");
            const company   = frappe.query_report.get_filter_value("company");
            const from_date = frappe.query_report.get_filter_value("from_date");
            const to_date   = frappe.query_report.get_filter_value("to_date");

            if (!supplier || !company || !from_date || !to_date) {
                frappe.msgprint(__("Please set Company, Supplier, From Date and To Date."));
                return;
            }

            const params = new URLSearchParams({
                supplier:      supplier,
                company:       company,
                from_date:     from_date,
                to_date:       to_date,
                show_ageing:   frappe.query_report.get_filter_value("show_ageing") || 0,
                include_draft: frappe.query_report.get_filter_value("include_draft") || 0,
                currency:      frappe.query_report.get_filter_value("currency") || "",
            });

            window.open(
                "/api/method/upande_accounting.upande_accounting_customizations.report."
                + "supplier_statement_of_account.supplier_statement_of_account.download_statement_pdf?"
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
            fieldname: "supplier",
            label: __("Supplier"),
            fieldtype: "Link",
            options: "Supplier",
            reqd: 1,
            on_change: function () {
                const supplier = frappe.query_report.get_filter_value("supplier");
                if (!supplier) return;
                frappe.db.get_value("Supplier", supplier, "default_currency", (r) => {
                    if (r && r.default_currency) {
                        frappe.query_report.set_filter_value("currency", r.default_currency);
                    }
                });
            },
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
            fieldname: "currency",
            label: __("Currency"),
            fieldtype: "Link",
            options: "Currency",
            default: frappe.defaults.get_user_default("currency"),
        },
        {
            fieldname: "include_draft",
            label: __("Include Draft Bills"),
            fieldtype: "Check",
            default: 0,
        },
        {
            fieldname: "show_ageing",
            label: __("Show Ageing Summary"),
            fieldtype: "Check",
            default: 0,
            on_change: function () {
                // Instantly toggle visibility without a full server re-run.
                // The Python also respects this flag — a manual Refresh will
                // fully add or remove ageing rows from the dataset.
                const show = frappe.query_report.get_filter_value("show_ageing");
                toggleAgeingRows(!!show);
            },
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

        // Ageing separator — section heading in the Document Type column
        if (data.is_separator && column.fieldname === "display_type") {
            value = `<span style="color:#888; font-size:0.85em; font-weight:600; letter-spacing:0.04em; text-transform:uppercase;">${value || ""}</span>`;
        }

        // Document type labels (normal bill rows)
        if (column.fieldname === "display_type" && !data.is_ageing && !data.is_separator && !data.is_opening && !data.is_closing) {
            if (data.display_type === "Debit Note") {
                value = `<span style="font-weight:300;">Debit Note</span>`;
            } else if (data.display_type === "Payment") {
                value = `<span style="font-weight:300;">Payment</span>`;
            } else if (data.display_type === "Bill") {
                value = `<span style="font-weight:300;">Bill</span>`;
            }
        }

        // Balance colour on bill rows — uses ageing_level computed in Python
        // against to_date, so "overdue" reflects the report date, not today.
        if (
            column.fieldname === "balance" &&
            data.voucher_type === "Purchase Invoice" &&
            flt(data.balance) > 0 &&
            data.ageing_level !== null && data.ageing_level !== undefined
        ) {
            // 5 distinct hue families: green → blue → amber → purple → red (critical)
            const colours = ["#27ae60", "#2980b9", "#f39c12", "#8e44ad", "#e74c3c"];
            const colour  = colours[Math.min(data.ageing_level, colours.length - 1)];
            value = `<span style="color:${colour}; font-weight:500;">${value}</span>`;
        }

        // Draft bill rows — orange italic
        if (data.is_draft) {
            value = `<span style="color:#e67e22; font-style:italic;">${value || ""}</span>`;
        }

        // Ageing rows — label in display_type, amount colour-coded by ageing_level
        if (data.is_ageing) {
            if (column.fieldname === "display_type") {
                value = `<em style="color:#555;">${value || ""}</em>`;
            }
            if (column.fieldname === "balance" && flt(data.balance) > 0) {
                // 5 distinct hue families: green → blue → amber → red → purple
                const colours = ["#27ae60", "#2980b9", "#f39c12", "#e74c3c", "#8e44ad"];
                const colour  = colours[Math.min(data.ageing_level || 0, colours.length - 1)];
                value = `<span style="color:${colour}; font-weight:600;">${value}</span>`;
            }
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
    // After render:
    //   1. Apply initial ageing visibility based on checkbox state
    //   2. Attach checkbox row-highlight listener
    // ------------------------------------------------------------------
    after_datatable_render: function (datatable) {

        // 1. Apply ageing visibility — use a short delay to let
        //    the DataTable finish painting all rows into the DOM.
        setTimeout(() => {
            const show = frappe.query_report.get_filter_value("show_ageing");
            // Treat undefined/null as "show" (default 1)
            toggleAgeingRows(show === undefined || show === null || show == 1);
        }, 100);

        // 2. Row highlight on checkbox selection
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

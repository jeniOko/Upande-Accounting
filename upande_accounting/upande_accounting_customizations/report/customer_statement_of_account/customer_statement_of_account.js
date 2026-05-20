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


frappe.query_reports["Customer Statement Of Account"] = {

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
            on_change: function () {
                const customer = frappe.query_report.get_filter_value("customer");
                if (!customer) return;
                frappe.db.get_value("Customer", customer, "default_currency", (r) => {
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
            fieldname: "show_ageing",
            label: __("Show Ageing Summary"),
            fieldtype: "Check",
            default: 1,
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

        // Opening / closing balance — bold
        if (data.is_opening || data.is_closing) {
            value = `<strong>${value || ""}</strong>`;
        }

        // Document type labels
        if (column.fieldname === "display_type") {
            if (data.display_type === "Credit Note") {
                value = `<span style="font-weight:300;">Credit Note</span>`;
            } else if (data.display_type === "Receipt") {
                value = `<span style="font-weight:300;">Receipt</span>`;
            } else if (data.display_type === "Invoice") {
                value = `<span style="font-weight:300;">Invoice</span>`;
            }
        }

        // Overdue balance on invoice rows
        if (
            column.fieldname === "balance" &&
            data.voucher_type === "Sales Invoice" &&
            !data.is_return &&
            data.due_date &&
            frappe.datetime.str_to_obj(data.due_date) < new Date() &&
            flt(data.balance) > 0
        ) {
            value = `<span style="color:#c0392b;">${value}</span>`;
        }

        // Ageing rows — muted italic label, colour-coded amount
        if (data.is_ageing) {
            if (column.fieldname === "description") {
                value = `<em style="color:#555;">${value || ""}</em>`;
            }
            if (column.fieldname === "balance" && flt(data.balance) > 0) {
                const label = (data.description || "").toLowerCase();
                let colour = "#27ae60";
                if      (label.includes("over 90")) colour = "#c0392b";
                else if (label.includes("61"))       colour = "#e74c3c";
                else if (label.includes("31"))       colour = "#e67e22";
                else if (label.includes("1 –"))      colour = "#f39c12";
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
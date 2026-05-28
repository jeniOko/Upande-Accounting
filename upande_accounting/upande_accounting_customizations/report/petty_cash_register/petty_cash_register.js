// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt


/**
 * Petty Cash Register
 * ====================
 * Cashbook-style report grouped by petty cash account.
 * Shows top-ups (Internal Transfer PE + JE debits) and
 * expense claim outflows with running balance.
 * Draft and submitted transactions included; cancelled excluded.
 */

frappe.query_reports["Petty Cash Register"] = {

    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
            reqd: 1,
            on_change: () => refresh_petty_cash_accounts(),
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
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
        // {
        //     fieldname: "petty_cash_account",
        //     label: __("Petty Cash Account"),
        //     fieldtype: "Link",
        //     options: "Account",
        //     get_query: function () {
        //         const f = frappe.query_report.get_filter("petty_cash_account");
        //         const allowed = (f && f._allowed_accounts) ? f._allowed_accounts : [];
        //         if (allowed.length) return { filters: { name: ["in", allowed] } };
        //         const company = frappe.query_report.get_filter_value("company");
        //         const filters = { account_type: ["in", ["Cash", "Bank"]], is_group: 0 };
        //         if (company) filters["company"] = company;
        //         return { filters };
        //     },
        // },
        {
            fieldname: "show_attachments_only",
            label: __("Show Missing Attachments Only"),
            fieldtype: "Check",
            default: 0,
            description: __("When checked, only expense claims without attachments are shown."),
        },
    ],

    onload: function (report) {
        refresh_petty_cash_accounts();
    },

    // ------------------------------------------------------------------
    // Row formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        // Separator — render blank
        if (data.is_separator) return "";

        // Account header — bold, uppercase, no other styling
        if (data.is_account_header) {
            if (column.fieldname === "description") {
                return `<strong style="text-transform:uppercase; letter-spacing:0.4px;">${value || ""}</strong>`;
            }
            return "";
        }

        // Opening / closing balance rows — bold
        if (data.is_opening || data.is_closing) {
            return `<strong>${value || ""}</strong>`;
        }

        // Status badge — minimal, only where it adds meaning
        if (column.fieldname === "doc_status") {
            // Expense Claims: docstatus=0 means submitted by employee, awaiting approval
            if (data.doc_type === "Expense Claim" && data.doc_status === "Draft") {
                return `<span style="background:#fef3cd; color:#856404; padding:1px 6px; border-radius:3px; font-size:11px; font-weight:600;">Pending Approval</span>`;
            }
            if (data.doc_status === "Pending Approval") {
                return `<span style="background:#fef3cd; color:#856404; padding:1px 6px; border-radius:3px; font-size:11px; font-weight:600;">Pending Approval</span>`;
            }
            if (data.doc_status === "Draft") {
                return `<span style="background:#f0f0f0; color:#555; padding:1px 6px; border-radius:3px; font-size:11px;">Draft</span>`;
            }
            if (data.doc_status === "Submitted") {
                return value; // no badge needed — submitted is the normal state
            }
        }

        // Attachment — only flag missing ones prominently
        if (column.fieldname === "has_attachment") {
            if (data.has_attachment === "No") {
                return `<span style="color:#c0392b;">✗ No</span>`;
            } else if ((data.has_attachment || "").startsWith("Yes")) {
                return `<span style="color:#27ae60; font-weight:600;">✓ ${data.has_attachment}</span>`;
            }
        }

        // Balance — red only when negative, otherwise plain
        if (column.fieldname === "balance" &&
            data.balance !== null && data.balance !== undefined &&
            flt(data.balance) < 0) {
            return `<span style="color:#c0392b; font-weight:600;">${value}</span>`;
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
// refresh_petty_cash_accounts
// ---------------------------------------------------------------------------
function refresh_petty_cash_accounts() {
    if (refresh_petty_cash_accounts._timer) clearTimeout(refresh_petty_cash_accounts._timer);
    refresh_petty_cash_accounts._timer = setTimeout(() => {
        const company = frappe.query_report.get_filter_value("company");
        if (!company) return;
        frappe.call({
            method: "upande_accounting.upande_accounting_customizations.report"
                  + ".petty_cash_register.petty_cash_register"
                  + ".get_petty_cash_accounts_for_filter",
            args: { company },
            callback: function (r) {
                if (!r.message) return;
                const f = frappe.query_report.get_filter("petty_cash_account");
                if (f) f._allowed_accounts = r.message;
            },
        });
    }, 400);
}
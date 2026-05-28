// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt


// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

/**
 * Withholding Tax Register
 * =========================
 * Tracks WHTAX + WHVAT obligations on purchase invoices.
 * Accounts are resolved via is_tax_report_account + tax_report_type fields.
 */

frappe.query_reports["Withholding Tax Register"] = {

    filters: [
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company"),
        },
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
        },
        {
            fieldname: "supplier",
            label: __("Supplier"),
            fieldtype: "Link",
            options: "Supplier",
        },
        {
            fieldname: "withholding_type",
            label: __("Withholding Type"),
            fieldtype: "Select",
            options: "\nWHTAX\nWHVAT",
        },
        {
            // Dynamically lists only accounts tagged as WHTAX or WHVAT.
            fieldname: "withholding_account",
            label: __("Withholding Account"),
            fieldtype: "Link",
            options: "Account",
            get_query: function () {
                const company = frappe.query_report.get_filter_value("company");
                const wh_type = frappe.query_report.get_filter_value("withholding_type");
                const filters = {
                    account_type:           "Tax",
                    is_tax_report_account:  1,
                };
                if (wh_type) {
                    filters["tax_report_type"] = wh_type;
                } else {
                    // Show both WHTAX and WHVAT accounts when no type selected
                    // ERPNext Link filter doesn't support OR natively;
                    // leave tax_report_type unset and let user pick any tagged account.
                }
                if (company) filters["company"] = company;
                return { filters };
            },
        },
        {
            fieldname: "payment_status",
            label: __("Payment Status"),
            fieldtype: "Select",
            options: "\nPaid\nUnpaid",
        },
    ],

    onload: function (report) {
        report.page.add_inner_button(__("Approve Suggested"), function () {
            select_suggested_rows(report);
        });

        report.page.add_inner_button(__("Unselect All"), function () {
            toggle_all_checkboxes(report, false);
        });

        report.page.add_inner_button(__("Process Payments"), function () {
            process_selected_payments(report);
        }).addClass("btn-primary");

        report.page.add_inner_button(__("Update PRN Numbers"), function () {
            batch_prn_update_dialog(report);
        });
    },

    onrefresh: function (report) {
        setTimeout(function () {
            enable_checkbox_functionality(report);
            enable_suggestion_checkbox(report);
        }, 500);
    },

    // ------------------------------------------------------------------
    // Freeze columns up to and including Supplier (column index 8)
    // so financial columns scroll while identity columns stay visible.
    // Frappe DataTable uses 0-based freeze_columns count.
    //
    // Column order:
    //   0  Select        1  Suggested       2  Withholding Type
    //   3  Tax Rate      4  KRA PIN         5  Supplier Invoice No
    //   6  Invoice Date  7  Supplier        ← freeze up to here (8 cols)
    //   8+ Nature of Transaction, amounts, status, etc. — these scroll
    // ------------------------------------------------------------------
    get_datatable_options(options) {
        return Object.assign(options, {
            freezeMessage: "",   // hide the default "Loading..." freeze overlay
            checkboxColumn: true,
            frozenColumnsCount: 8,
        });
    },

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (column.fieldname === "select_row") {
            const is_paid    = data.payment_status === "Paid";
            const unique_key = (data.invoice_number || "") + "||" + (data.withholding_account || "");
            return '<input type="checkbox"'
                + ' class="report-checkbox"'
                + ' data-unique-key="'           + unique_key                             + '"'
                + ' data-invoice="'              + (data.invoice_number || "")            + '"'
                + ' data-bill-date="'            + (data.bill_date || "")                 + '"'
                + ' data-bill-no="'              + (data.bill_no || "")                   + '"'
                + ' data-account="'              + (data.withholding_account || "")       + '"'
                + ' data-supplier="'             + (data.supplier || "")                  + '"'
                + ' data-base-amount="'          + (data.base_amount || 0)                + '"'
                + ' data-net-amount="'           + (data.base_net_amount || 0)            + '"'
                + ' data-amount="'               + (data.withheld_amount || 0)            + '"'
                + ' data-amount-transaction="'   + (data.withheld_amount_transaction || 0) + '"'
                + ' data-currency="'             + (data.transaction_currency || "KES")   + '"'
                + ' data-exchange-rate="'        + (data.exchange_rate || 1)              + '"'
                + ' data-type="'                 + (data.withholding_type || "")          + '"'
                + (is_paid ? " disabled" : "")
                + ">";
        }

        if (column.fieldname === "suggest_payment") {
            const is_paid     = data.payment_status === "Paid";
            const is_checked  = data.suggest_payment ? "checked" : "";
            const is_disabled = (!data.wtp_record_name || is_paid) ? "disabled" : "";
            return '<input type="checkbox"'
                + ' class="suggest-checkbox"'
                + ' data-wtp-name="' + (data.wtp_record_name || "") + '"'
                + " " + is_checked
                + " " + is_disabled
                + ">";
        }

        if (column.fieldname === "payment_status") {
            if (data.payment_status === "Paid") {
                value = '<span style="color:green; font-weight:bold;">Paid</span>';
            } else if (data.payment_status === "Unpaid") {
                value = '<span style="color:red; font-weight:bold;">Unpaid</span>';
            }
        }

        if (column.fieldname === "withholding_type_display") {
            if (data.withholding_type_display === "WHTAX") {
                value = '<span style="color:#1a6ebd; font-weight:600;">WHTAX</span>';
            } else if (data.withholding_type_display === "WHVAT") {
                value = '<span style="color:#e67e22; font-weight:600;">WHVAT</span>';
            }
        }

        return value;
    },
};


// ---------------------------------------------------------------------------
// Checkbox handlers
// ---------------------------------------------------------------------------

function enable_checkbox_functionality(report) {
    $(document).off("click", ".report-checkbox");
    $(document).on("click", ".report-checkbox", function (e) { e.stopPropagation(); });
}

function enable_suggestion_checkbox(report) {
    $(document).off("click", ".suggest-checkbox");
    $(document).on("click", ".suggest-checkbox", function (event) {
        event.stopPropagation();
        const checkbox = $(this);
        const wtp_name = checkbox.data("wtp-name");
        const value    = checkbox.is(":checked") ? 1 : 0;

        if (!wtp_name) {
            frappe.msgprint({
                title: __("Missing Record"),
                message: __("No linked Withholding Tax Payment record found."),
                indicator: "red",
            });
            checkbox.prop("checked", !value);
            return;
        }

        frappe.call({
            method: "upande_accounting.upande_accounting_customizations.report"
                  + ".withholding_tax_register.withholding_tax_register"
                  + ".update_suggestion_flag",
            args: { wtp_name, value },
            callback: function (r) {
                if (r.message && r.message.status === "success") {
                    frappe.show_alert({ message: __("Suggestion updated"), indicator: "green" }, 2);
                    if (report.data) {
                        report.data.forEach(row => {
                            if (row.wtp_record_name === wtp_name) row.suggest_payment = value;
                        });
                    }
                } else {
                    checkbox.prop("checked", !value);
                }
            },
            error: () => checkbox.prop("checked", !value),
        });
    });
}

function select_suggested_rows(report) {
    if (!report.data || !report.data.length) {
        frappe.msgprint({ title: __("No Data"), message: __("Run the report first."), indicator: "orange" });
        return;
    }
    let newly = 0, already = 0, skipped = 0, offscreen = 0;

    report.data.forEach(row => {
        if (!row.suggest_payment) return;
        if (row.payment_status === "Paid") { skipped++; return; }

        const key = (row.invoice_number || "") + "||" + (row.withholding_account || "");
        const $cb = report.$report.find('.report-checkbox[data-unique-key="' + key + '"]');
        if ($cb.length) {
            if (!$cb.is(":checked")) { $cb.prop("checked", true); newly++; }
            else already++;
        } else {
            row._force_selected = true; offscreen++; newly++;
        }
    });

    let msg = newly + " suggested row(s) selected.";
    if (already)   msg += " " + already   + " already selected.";
    if (skipped)   msg += " " + skipped   + " skipped (paid).";
    if (offscreen) msg += " " + offscreen + " off-screen row(s) queued.";
    frappe.show_alert({ message: __(msg), indicator: "green" }, 4);
}

function toggle_all_checkboxes(report, check) {
    const cbs = report.$report.find(".report-checkbox:not(:disabled)");
    cbs.each(function () { $(this).prop("checked", check); });
    if (!check && report.data) report.data.forEach(row => delete row._force_selected);
    frappe.show_alert({
        message: __(cbs.length + " row(s) " + (check ? "selected" : "unselected")),
        indicator: "blue",
    }, 3);
}

function get_selected_rows(report) {
    const selected = [];
    const seen     = new Set();

    report.$report.find(".report-checkbox:checked").each(function () {
        const $cb  = $(this);
        const key  = $cb.data("unique-key");
        if (seen.has(key)) return;

        const inv  = $cb.data("invoice");
        const acct = $cb.data("account");
        let matched = null;

        if (report.data) {
            report.data.forEach(row => {
                if (row.invoice_number === inv && row.withholding_account === acct) matched = row;
            });
        }

        selected.push(matched || {
            invoice_number:           inv,
            bill_no:                  $cb.data("bill-no"),
            withholding_account:      acct,
            supplier:                 $cb.data("supplier"),
            base_amount:              parseFloat($cb.data("base-amount")) || 0,
            base_net_amount:          parseFloat($cb.data("net-amount"))  || 0,
            withheld_amount:          parseFloat($cb.data("amount"))      || 0,
            withheld_amount_transaction: parseFloat($cb.data("amount-transaction")) || 0,
            transaction_currency:     $cb.data("currency")    || "KES",
            exchange_rate:            parseFloat($cb.data("exchange-rate")) || 1,
            withholding_type:         $cb.data("type"),
            bill_date:                $cb.data("bill-date"),
            payment_status:           "Unpaid",
        });
        seen.add(key);
    });

    // Off-screen rows
    if (report.data) {
        report.data.forEach(row => {
            if (!row._force_selected) return;
            const key = (row.invoice_number || "") + "||" + (row.withholding_account || "");
            if (!seen.has(key)) { selected.push(row); seen.add(key); }
        });
    }
    return selected;
}

function process_selected_payments(report) {
    const rows = get_selected_rows(report);
    if (!rows.length) {
        frappe.msgprint({ title: __("No Selection"), message: __("Select at least one row."), indicator: "orange" });
        return;
    }
    show_payment_dialog(rows, report);
}

function show_payment_dialog(selected_rows, report) {
    const total   = selected_rows.reduce((s, r) => s + flt(r.withheld_amount), 0);
    const company = report.get_filter_value("company");

    const dialog = new frappe.ui.Dialog({
        title: __("Process Withholding Tax Payments"),
        fields: [
            {
                fieldname: "payment_summary", fieldtype: "HTML",
                options: `<div style="background:#f8f9fa;padding:15px;border-radius:5px;margin-bottom:15px;">
                    <h5>Batch Payment Details</h5>
                    <p><strong>Invoices:</strong> ${selected_rows.length}</p>
                    <p><strong>Total Amount:</strong> KES ${format_currency(total)}</p>
                    <p><small>Creates a single journal entry for all selected payments.</small></p>
                </div>`,
            },
            { fieldname: "sb1", fieldtype: "Section Break" },
            {
                fieldname: "bank_account", label: __("KES Bank Account"),
                fieldtype: "Link", options: "Account", reqd: 1,
                get_query: () => ({
                    filters: { account_type: "Bank", is_group: 0, account_currency: "KES",
                               ...(company ? { company } : {}) },
                }),
            },
            { fieldname: "cb1", fieldtype: "Column Break" },
            { fieldname: "reference_number", label: __("Reference Number"), fieldtype: "Data",
              description: __("Cheque or transfer reference") },
            { fieldname: "sb2", fieldtype: "Section Break" },
            { fieldname: "reference_date", label: __("Reference Date"), fieldtype: "Date",
              default: frappe.datetime.get_today() },
            { fieldname: "cb2", fieldtype: "Column Break" },
            { fieldname: "user_remark", label: __("Remarks"), fieldtype: "Small Text" },
        ],
        size: "large",
        primary_action_label: __("Create Journal Entry"),
        primary_action: function (values) {
            if (!values.bank_account) { frappe.msgprint(__("Select a KES bank account")); return; }
            dialog.hide();
            frappe.call({
                method: "upande_accounting.upande_accounting_customizations.report"
                      + ".withholding_tax_register.withholding_tax_register"
                      + ".process_withholding_payments",
                args: {
                    selected_rows:    selected_rows,
                    bank_account:     values.bank_account,
                    reference_number: values.reference_number,
                    reference_date:   values.reference_date,
                    user_remark:      values.user_remark,
                },
                callback: function (r) {
                    if (r.message && r.message.status === "success") {
                        if (report.data) report.data.forEach(row => delete row._force_selected);
                        frappe.msgprint({
                            title: __("Payment Processed"),
                            message: `<strong>Journal Entry:</strong> <a href="/app/journal-entry/${r.message.journal_entry}" target="_blank">${r.message.journal_entry}</a><br>${r.message.message}`,
                            indicator: "green",
                        });
                        report.refresh();
                    }
                },
            });
        },
    });
    dialog.show();
}

function batch_prn_update_dialog(report) {
    const pending = (report.data || []).filter(
        r => r.payment_status === "Paid" && (!r.prn_number || !r.prn_number.trim())
    );
    if (!pending.length) {
        frappe.msgprint({ title: __("No Pending PRN Updates"),
            message: __("All paid entries already have PRN numbers."), indicator: "blue" });
        return;
    }

    let html = `<div style="margin-bottom:15px;">
        <p><strong>Update PRN numbers for ${pending.length} paid entries:</strong></p>
        <table class="table table-bordered" style="margin-top:10px;">
        <thead><tr><th>Bill No</th><th>Supplier</th><th>Amount (KES)</th><th>PRN Number</th></tr></thead><tbody>`;

    pending.forEach(r => {
        const wtp_name = r.wtp_record_name || r.wtp_name;
        if (!wtp_name) return;
        html += `<tr>
            <td>${r.bill_no || ""}</td>
            <td>${r.supplier || ""}</td>
            <td>KES ${format_currency(r.withheld_amount || 0)}</td>
            <td><input type="text" class="batch-prn-input form-control"
                data-wtp-name="${wtp_name}" placeholder="Enter PRN" style="width:100%;"></td>
        </tr>`;
    });
    html += "</tbody></table></div>";

    const batch_dialog = new frappe.ui.Dialog({
        title: __("Batch Update PRN Numbers"),
        fields: [{ fieldname: "batch_prn_html", fieldtype: "HTML", options: html }],
        size: "large",
        primary_action_label: __("Save PRN Numbers"),
        primary_action: function () {
            const updates = [];
            $(".batch-prn-input").each(function () {
                const prn  = $(this).val().trim();
                const name = $(this).data("wtp-name");
                if (prn && name) updates.push({ name, prn_number: prn });
            });
            if (!updates.length) {
                frappe.msgprint({ title: __("No Updates"),
                    message: __("Enter at least one PRN number."), indicator: "orange" });
                return;
            }
            frappe.call({
                method: "upande_accounting.upande_accounting_customizations.report"
                      + ".withholding_tax_register.withholding_tax_register"
                      + ".batch_update_prn_numbers",
                args: { prn_updates: updates },
                callback: function (r) {
                    if (r.message) {
                        frappe.msgprint({ title: __("Batch Update Complete"),
                            message: r.message.message,
                            indicator: r.message.status === "success" ? "green" : "orange" });
                        report.refresh();
                        batch_dialog.hide();
                    }
                },
            });
        },
    });
    batch_dialog.show();
}

function format_currency(amount) {
    return Number(amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
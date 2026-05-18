// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt


frappe.query_reports["Payment Register"] = {

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
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
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
            fieldname: "payment_type",
            label: __("Payment Type"),
            fieldtype: "Select",
            options: "\nReceive\nPay",
        },
        {
            fieldname: "party_type",
            label: __("Party Type"),
            fieldtype: "Select",
            options: "\nCustomer\nSupplier",
            on_change: function () {
                const partyType = frappe.query_report.get_filter_value("party_type");
                frappe.query_report.set_filter_value("party", "");
                frappe.query_report.get_filter("party").df.options = partyType || "Customer";
                frappe.query_report.get_filter("party").refresh();
            },
        },
        {
            fieldname: "party",
            label: __("Party"),
            fieldtype: "Link",
            options: "Customer",
            get_query: function () {
                const partyType =
                    frappe.query_report.get_filter_value("party_type") || "Customer";
                return { doctype: partyType };
            },
        },
        {
            fieldname: "mode_of_payment",
            label: __("Mode of Payment"),
            fieldtype: "Link",
            options: "Mode of Payment",
        },
    ],

    // ------------------------------------------------------------------
    // Row formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (!data) return value;

        // Colour-code payment direction
        if (column.fieldname === "payment_type") {
            if (data.payment_type === "Receive") {
                value = `<span style="color:green; font-weight:600;">Receive</span>`;
            } else if (data.payment_type === "Pay") {
                value = `<span style="color:#c0392b; font-weight:600;">Pay</span>`;
            }
        }

        // Highlight rows that still carry an unallocated balance
        if (column.fieldname === "unallocated_amount" && flt(data.unallocated_amount) > 0) {
            value = `<span style="color:darkorange; font-weight:600;">${value}</span>`;
        }

        return value;
    },

    // ------------------------------------------------------------------
    // Checkbox column — keep it enabled
    // ------------------------------------------------------------------
    get_datatable_options(options) {
        return Object.assign(options, {
            checkboxColumn: true,
        });
    },

    
    after_datatable_render: function (datatable) {
        const HIGHLIGHT_BG     = "#f5e879ff";           // soft yellow
        const HIGHLIGHT_BORDER = "2px solid #f5a623"; // amber border

       
        const wrapper = (datatable.wrapper)
            || (datatable.$el && datatable.$el[0])
            || (datatable.bodyScrollable && datatable.bodyScrollable.closest(".datatable"));

        if (!wrapper) return;

        if (wrapper.__highlightListenerAttached) return;
        wrapper.__highlightListenerAttached = true;

        wrapper.addEventListener("click", function (e) {
            const checkbox = e.target.closest("input[type='checkbox']");
            if (!checkbox) return;

            // Walk up to the <tr> that owns this checkbox
            const tr = checkbox.closest("tr");
            if (!tr) return;

            // Skip the header / select-all row
            if (tr.closest("thead")) return;

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
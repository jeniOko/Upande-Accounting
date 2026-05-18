// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt


frappe.query_reports["Advance Payment Register"] = {

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
            fieldname: "party_type",
            label: __("Party Type"),
            fieldtype: "Select",
            options: "\nCustomer\nSupplier",
            // When party type changes, reset the party field and update its
            // doctype so only matching records are shown.
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
            options: "Customer",   // updated dynamically by party_type on_change
            get_query: function () {
                const partyType =
                    frappe.query_report.get_filter_value("party_type") || "Customer";
                return { doctype: partyType };
            },
        },
        {
            fieldname: "payment_status",
            label: __("Payment Status"),
            fieldtype: "Select",
            options: "\nFully Allocated\nPartially Allocated\nUnallocated",
        },
        {
            fieldname: "advance_type",
            label: __("Advance Type"),
            fieldtype: "Select",
            options: "\nOrder-Based Advance\nUnallocated Advance\nPartially Used Advance",
        },
    ],

    // ------------------------------------------------------------------
    // Conditional row formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (!data) return value;

        if (column.fieldname === "payment_status") {
            const colours = {
                "Fully Allocated":     "green",
                "Partially Allocated": "orange",
                "Unallocated":         "red",
            };
            const colour = colours[data.payment_status];
            if (colour) {
                value = `<span style="color:${colour}; font-weight:600;">${data.payment_status}</span>`;
            }
        }

        if (column.fieldname === "advance_type") {
            const colours = {
                "Order-Based Advance":    "#1a6ebd",   // blue
                "Unallocated Advance":    "#c0392b",   // red
                "Partially Used Advance": "#e67e22",   // orange
            };
            const colour = colours[data.advance_type];
            if (colour) {
                value = `<span style="color:${colour}; font-weight:600;">${data.advance_type}</span>`;
            }
        }

        // Highlight unallocated balance > 0 in amber
        if (column.fieldname === "unallocated_amount" && flt(data.unallocated_amount) > 0) {
            value = `<span style="color:darkorange;">${value}</span>`;
        }

        return value;
    },

    // ------------------------------------------------------------------
    // Quick summary row at the bottom
    // ------------------------------------------------------------------
    get_datatable_options(options) {
        return Object.assign(options, {
            checkboxColumn: true,
        });
    },
};
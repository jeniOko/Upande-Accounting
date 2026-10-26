// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

// A MultiSelectList with a fixed set of choices
function fixed_choices(choices) {
    return function (txt) {
        txt = (txt || "").toLowerCase();
        return choices
            .filter((c) => c.toLowerCase().includes(txt))
            .map((c) => ({ value: c, description: "" }));
    };
}

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
            // A year back: an advance stays open until it is invoiced, often
            // well beyond the month it was paid in.
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
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
            fieldtype: "Link",
            options: "Party Type",
            on_change: function () {
                frappe.query_report.set_filter_value("party", []);
            },
        },
        {
            fieldname: "party",
            label: __("Party"),
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                const party_type = frappe.query_report.get_filter_value("party_type");
                if (party_type) {
                    return frappe.db.get_link_options(party_type, txt);
                }
                return Promise.all([
                    frappe.db.get_link_options("Customer", txt),
                    frappe.db.get_link_options("Supplier", txt),
                ]).then((lists) => lists.flat());
            },
        },
        {
            fieldname: "group_by",
            label: __("Group By"),
            fieldtype: "Select",
            options: [
                "",
                "Group by Currency",
                "Group by Party Type",
                "Group by Party",
                "Group by Goods / Services / Assets",
                "Group by Delivery Status",
                "Group by Allocation Status",
                "Group by Advance Linked To",
            ].join("\n"),
        },
        {
            fieldname: "allocation_status",
            label: __("Allocated To Invoices"),
            fieldtype: "MultiSelectList",
            default: ["Unallocated", "Partially Allocated"],
            description: __(
                "How much of the advance has been set off against invoices. An advance linked to an order but not yet invoiced counts as Unallocated."
            ),
            get_data: fixed_choices(["Unallocated", "Partially Allocated", "Fully Allocated"]),
        },
        {
            fieldname: "linked_to",
            label: __("Advance Linked To"),
            fieldtype: "MultiSelectList",
            description: __("Which document the advance was paid against."),
            get_data: fixed_choices(["Sales / Purchase Order", "Invoice Only", "Nothing Yet"]),
        },
        {
            fieldname: "advance_for",
            label: __("Goods / Services / Assets"),
            fieldtype: "MultiSelectList",
            description: __(
                "From the items on the linked orders and invoices. Shows any advance that includes a chosen kind, so Assets also shows an advance for goods and assets together. Not Specified means nothing is linked yet."
            ),
            get_data: fixed_choices(["Goods", "Services", "Assets", "Not Specified"]),
        },
        {
            fieldname: "delivery_status",
            label: __("Delivery Status"),
            fieldtype: "MultiSelectList",
            description: __(
                "From the linked orders: goods by delivered / received qty, services by billed amount."
            ),
            get_data: fixed_choices([
                "Not Delivered", "Partly Delivered", "Fully Delivered", "Invoiced", "Not Linked",
            ]),
        },
    ],

    // ------------------------------------------------------------------
    // Conditional row formatting
    // ------------------------------------------------------------------
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (!data) return value;

        // Group header and sub-total rows
        if (data.bold) {
            return `<strong>${value}</strong>`;
        }

        const colours = {
            allocation_status: {
                "Fully Allocated":     "green",
                "Partially Allocated": "orange",
                "Unallocated":         "red",
            },
            delivery_status: {
                "Fully Delivered":  "green",
                "Invoiced":         "green",
                "Partly Delivered": "orange",
                "Not Delivered":    "red",
                "Not Linked":       "grey",
            },
            advance_for: {
                "Goods":            "#1a6ebd",
                "Services":         "#7c3aed",
                "Assets":           "#b45309",
                "Not Specified":    "grey",
            },
            linked_to: {
                "Sales / Purchase Order": "#1a6ebd",
                "Invoice Only":           "#0f766e",
                "Nothing Yet":            "#c0392b",
            },
        };
        let colour = colours[column.fieldname] && colours[column.fieldname][data[column.fieldname]];
        // Mixed kinds, e.g. "Goods & Assets"
        if (!colour && column.fieldname === "advance_for" && data.advance_for) {
            colour = "#0f766e";
        }
        if (colour) {
            value = `<span style="color:${colour}; font-weight:600;">${value}</span>`;
        }

        if (column.fieldname === "open_balance" && flt(data.open_balance) > 0) {
            value = `<span style="color:darkorange; font-weight:600;">${value}</span>`;
        }

        return value;
    },

    // ------------------------------------------------------------------
    // Checkbox column
    // ------------------------------------------------------------------
    get_datatable_options(options) {
        return Object.assign(options, {
            checkboxColumn: true,
        });
    },

    // ------------------------------------------------------------------
    // Row highlight on checkbox selection
    // ------------------------------------------------------------------
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
            if (!tr) return;

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

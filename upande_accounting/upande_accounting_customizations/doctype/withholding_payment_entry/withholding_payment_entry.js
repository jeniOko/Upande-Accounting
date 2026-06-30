// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Withholding Payment Entry", {

	setup(frm) {
		frm.set_query("paid_to", function () {
			return {
				filters: {
					account_type: "Tax",
					is_tax_report_account: 1,
					is_group: 0,
					company: frm.doc.company,
				},
			};
		});
	},

	company(frm) {
		frm.set_value("paid_from", "");
		frm.set_value("paid_to", "");
		_set_paid_from_query(frm);
	},

	refresh(frm) {
		_set_paid_from_query(frm);

		// Hide Add/Delete on the entries table — rows only come from the fetch
		if (frm.fields_dict.withholding_entries) {
			frm.fields_dict.withholding_entries.grid.cannot_add_rows = true;
			frm.fields_dict.withholding_entries.grid.cannot_delete_rows =
				frm.doc.docstatus > 0;
		}
	},

	paid_to(frm) {
		if (frm.doc.paid_to && frm.doc.company) {
			frm.trigger("fetch_outstanding");
		}
	},

	get_outstanding_records(frm) {
		frm.trigger("fetch_outstanding");
	},

	fetch_outstanding(frm) {
		if (!frm.doc.paid_to || !frm.doc.company) {
			frappe.msgprint(__("Please select Company and Paid To account first."));
			return;
		}

		frappe.call({
			method:
				"upande_accounting.upande_accounting_customizations.doctype.withholding_payment_entry.withholding_payment_entry.get_outstanding_wtm_records",
			args: {
				paid_to_account: frm.doc.paid_to,
				company: frm.doc.company,
			},
			callback(r) {
				frm.clear_table("withholding_entries");

				if (!r.message || !r.message.length) {
					frappe.msgprint({
						title: __("No Outstanding Records"),
						message: __(
							"No unpaid withholding records found for account <b>{0}</b>.",
							[frm.doc.paid_to]
						),
						indicator: "orange",
					});
					frm.refresh_field("withholding_entries");
					frm.set_value("paid_amount", 0);
					return;
				}

				r.message.forEach(function (row) {
					let child = frm.add_child("withholding_entries");
					child.wtm_reference = row.name;
					child.supplier = row.supplier;
					child.invoice_number = row.invoice_number;
					child.withholding_category = row.withholding_category;
					child.withheld_amount = row.withheld_amount;
				});

				frm.refresh_field("withholding_entries");
				_recalculate_paid_amount(frm);

				frappe.show_alert({
					message: __("{0} record(s) fetched.", [r.message.length]),
					indicator: "green",
				});
			},
		});
	},
});

frappe.ui.form.on("WPE Withholding Entry", {
	withheld_amount() {
		// Recalculate total when any row changes (shouldn't happen since read_only,
		// but keep as a safety net)
		const frm = cur_frm;
		_recalculate_paid_amount(frm);
	},
	withholding_entries_remove() {
		_recalculate_paid_amount(cur_frm);
	},
});

// ---------------------------------------------------------------------------

function _set_paid_from_query(frm) {
	if (!frm.doc.company) return;

	frappe.db.get_value("Company", frm.doc.company, "default_currency", function (r) {
		frm.set_query("paid_from", function () {
			return {
				filters: {
					account_type: ["in", ["Bank", "Cash"]],
					account_currency: r.default_currency,
					is_group: 0,
					company: frm.doc.company,
				},
			};
		});
	});
}

function _recalculate_paid_amount(frm) {
	const total = (frm.doc.withholding_entries || []).reduce(
		(sum, row) => sum + (row.withheld_amount || 0),
		0
	);
	frm.set_value("paid_amount", total);
}

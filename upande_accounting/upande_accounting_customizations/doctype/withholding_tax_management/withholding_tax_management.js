// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

frappe.ui.form.on("Withholding Tax Management", {
	refresh(frm) {
		if (frm.doc.suggested_for_payment && frm.doc.payment_status === "Unpaid") {
			frm.add_custom_button(
				__("Withholding Payment Entry"),
				function () {
					_create_payment_entry([frm.doc.name]);
				},
				__("Create")
			);
		}
	},
});


// ---------------------------------------------------------------------------
// List view — bulk action
// ---------------------------------------------------------------------------

frappe.listview_settings["Withholding Tax Management"] = {
	onload(listview) {
		listview.page.add_action_item(__("Create Payment Entry"), function () {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__("Please select at least one record."));
				return;
			}
			_create_payment_entry(selected.map((r) => r.name));
		});
	},
};


// ---------------------------------------------------------------------------

function _create_payment_entry(wtm_names) {
	frappe.call({
		method: "upande_accounting.upande_accounting_customizations.doctype.withholding_payment_entry.withholding_payment_entry.create_from_wtm",
		args: { wtm_names: JSON.stringify(wtm_names) },
		freeze: true,
		freeze_message: __("Preparing Payment Entry…"),
		callback(r) {
			if (r.message) {
				frappe.set_route("Form", "Withholding Payment Entry", r.message);
			}
		},
	});
}

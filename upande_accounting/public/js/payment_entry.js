frappe.ui.form.on("Payment Entry Reference", {
	reference_name: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.reference_doctype !== "Purchase Invoice" || !row.reference_name) {
			frappe.model.set_value(cdt, cdn, "custom_cu_invoice_no", "");
			return;
		}
		frappe.db
			.get_value("Purchase Invoice", row.reference_name, "custom_control_unit_invoice_number")
			.then((r) => {
				const val = (r && r.message && r.message.custom_control_unit_invoice_number) || "";
				frappe.model.set_value(cdt, cdn, "custom_cu_invoice_no", val);
			});
	},
});

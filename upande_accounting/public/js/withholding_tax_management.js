frappe.ui.form.on("Withholding Tax Management", {
	refresh(frm) {
		// Hide Add Row / Delete Row — table is managed by Payment Entry hooks
		if (frm.fields_dict.payment_references) {
			frm.fields_dict.payment_references.grid.cannot_add_rows = true;
			frm.fields_dict.payment_references.grid.cannot_delete_rows = true;
		}

		// Only show unreconcile button when there are rows to remove
		const has_refs = (frm.doc.payment_references || []).length > 0;
		frm.toggle_display("unreconcile_payment", has_refs);
	},

	unreconcile_payment(frm) {
		const refs = frm.doc.payment_references || [];
		if (!refs.length) {
			frappe.msgprint(__("No payment references to unreconcile."));
			return;
		}

		const row_name_map = {};
		const select_options = refs
			.map((r) => {
				const amount_str = r.currency
					? `${format_currency(r.allocated_amount)} ${r.currency}`
					: format_currency(r.allocated_amount);
				const label = `${r.reference_name} — ${amount_str}`;
				row_name_map[label] = r.name;
				return label;
			})
			.join("\n");

		frappe.prompt(
			[
				{
					fieldname: "selected",
					fieldtype: "Select",
					label: __("Select Payment Entry to Remove"),
					options: select_options,
					reqd: 1,
				},
			],
			(values) => {
				const row_name = row_name_map[values.selected];
				if (!row_name) return;

				frappe.confirm(
					__(
						"Remove this payment reference from the record? This does not affect the Payment Entry itself."
					),
					() => {
						frappe.call({
							method: "upande_accounting.withholding_tax_management.unreconcile_payment",
							args: { wtm_name: frm.doc.name, row_name },
							callback(r) {
								if (!r.exc) {
									frm.reload_doc();
									frappe.show_alert({
										message: __("Payment reference removed."),
										indicator: "green",
									});
								}
							},
						});
					}
				);
			},
			__("Unreconcile Payment"),
			__("Remove")
		);
	},
});

function format_currency(amount) {
	return frappe.format(amount, { fieldtype: "Currency" });
}

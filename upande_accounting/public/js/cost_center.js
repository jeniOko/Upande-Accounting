frappe.ui.form.on("Cost Center", {
	setup(frm) {
		frappe
			.call("upande_accounting.cost_center_dimensions.get_relatable_dimensions")
			.then((r) => {
				const doctypes = (r.message || []).map((d) => d.document_type);
				frm.set_query("accounting_dimension", "custom_related_dimensions", () => ({
					filters: { name: ["in", doctypes] },
				}));
			});
	},
});

frappe.ui.form.on("Cost Center Related Dimension", {
	accounting_dimension(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "dimension_value", null);
	},
});

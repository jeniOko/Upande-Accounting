const ITEM_TYPE_MSG = __(
	"Please define the item type. Check one of:"
	+ "<br><br>&bull;&nbsp;<b>Maintain Stock</b>"
	+ "<br>&bull;&nbsp;<b>Is Fixed Asset</b>"
	+ "<br>&bull;&nbsp;<b>Is Service Item</b>"
);

function item_type_is_missing(doc) {
	return !doc.is_stock_item && !doc.is_fixed_asset && !doc.custom_is_service_item;
}

// Validate item type before Quick Entry save
frappe.ui.form.ItemQuickEntryForm = class ItemQuickEntryForm extends frappe.ui.form.QuickEntryForm {
	insert() {
		const values = this.dialog.get_values();
		if (item_type_is_missing(values)) {
			frappe.unfreeze();
			frappe.msgprint({ title: __("Item Type Required"), message: ITEM_TYPE_MSG, indicator: "red" });
			return;
		}
		return super.insert();
	}
};

frappe.ui.form.on("Item", {
	validate: function (frm) {
		if (item_type_is_missing(frm.doc)) {
			frappe.throw(ITEM_TYPE_MSG);
		}
	},
	custom_is_service_item: function (frm) {
		if (frm.doc.custom_is_service_item && frm.doc.is_stock_item) {
			frm.set_value("is_stock_item", 0);
		}
	},
	is_stock_item: function (frm) {
		if (!frm.doc.is_stock_item && !frm.doc.is_fixed_asset) {
			if (!frm.doc.custom_is_service_item) {
				frm.set_value("custom_is_service_item", 1);
			}
		} else if (frm.doc.is_stock_item && frm.doc.custom_is_service_item) {
			frm.set_value("custom_is_service_item", 0);
		}
	},
	is_fixed_asset: function (frm) {
		if (!frm.doc.is_stock_item && !frm.doc.is_fixed_asset) {
			if (!frm.doc.custom_is_service_item) {
				frm.set_value("custom_is_service_item", 1);
			}
		} else if (frm.doc.is_fixed_asset && frm.doc.custom_is_service_item) {
			frm.set_value("custom_is_service_item", 0);
		}
	},
});

// Product Cost Analysis: Cost Sources may point to Cost Center, Project or any enabled Accounting Dimension.
frappe.ui.form.on("Item", {
	setup(frm) {
		frappe
			.call("upande_accounting.product_cost_analysis.get_cost_source_dimensions")
			.then((r) => {
				const doctypes = (r.message || []).map((d) => d.document_type);
				frm.set_query("accounting_dimension", "cost_dimensions", () => ({
					filters: { name: ["in", doctypes] },
				}));
			});
	},
	track_cost_analysis(frm) {
		if (frm.doc.track_cost_analysis) {
			if (!frm.doc.reporting_uom) frm.set_value("reporting_uom", frm.doc.stock_uom);
			if (!frm.doc.reporting_conversion_factor) frm.set_value("reporting_conversion_factor", 1);
			if (!frm.doc.allocation_basis) frm.set_value("allocation_basis", "Production Qty");
		}
	},
	allocation_basis(frm) {
		if (frm.doc.allocation_basis !== "Fixed %") frm.set_value("allocation_percentage", 0);
	},
});

frappe.ui.form.on("Cost Center Related Dimension", {
	accounting_dimension(frm, cdt, cdn) {
		if (frm.doctype === "Item") frappe.model.set_value(cdt, cdn, "dimension_value", null);
	},
});

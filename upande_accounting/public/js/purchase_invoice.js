frappe.ui.form.on("Purchase Invoice", {
	refresh: function (frm) {
		_set_withholding_queries(frm);
		_set_item_withholding_override_query(frm);
	},

	apply_multiple_withholding: function (frm) {
		if (!frm.doc.apply_multiple_withholding) {
			frm.set_value("custom_withholding_count", "");
			frm.set_value("custom_withholding_1", "");
			frm.set_value("custom_withholding_2", "");
			frm.set_value("custom_withholding_3", "");
		} else if (!frm.doc.custom_withholding_count) {
			frm.set_value("custom_withholding_count", "1");
		}
	},

	custom_withholding_count: function (frm) {
		const count = parseInt(frm.doc.custom_withholding_count) || 0;
		if (count < 3) frm.set_value("custom_withholding_3", "");
		if (count < 2) frm.set_value("custom_withholding_2", "");
	},
});

frappe.ui.form.on("Purchase Invoice Item", {
	custom_override_withholding: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.custom_override_withholding) {
			frappe.model.set_value(cdt, cdn, "custom_withholding_action", "");
			frappe.model.set_value(cdt, cdn, "custom_withholding_override_category", "");
		}
	},
});

function _set_withholding_queries(frm) {
	["custom_withholding_1", "custom_withholding_2", "custom_withholding_3"].forEach(function (fieldname) {
		frm.set_query(fieldname, function () {
			const excluded = [];
			if (frm.doc.tax_withholding_category) excluded.push(frm.doc.tax_withholding_category);
			["custom_withholding_1", "custom_withholding_2", "custom_withholding_3"].forEach(function (f) {
				if (f !== fieldname && frm.doc[f]) excluded.push(frm.doc[f]);
			});
			return excluded.length ? { filters: [["name", "not in", excluded]] } : {};
		});
	});
}

function _set_item_withholding_override_query(frm) {
	frm.set_query("custom_withholding_override_category", "items", function (doc) {
		const active = ["tax_withholding_category", "custom_withholding_1", "custom_withholding_2", "custom_withholding_3"]
			.map((f) => doc[f])
			.filter(Boolean);
		return { filters: [["name", "in", active.length ? active : ["__none__"]]] };
	});
}

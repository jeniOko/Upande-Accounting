frappe.ui.form.on("Purchase Invoice", {
	refresh: function (frm) {
		_set_withholding_queries(frm);
	},

	apply_multiple_withholding: function (frm) {
		if (!frm.doc.apply_multiple_withholding) {
			frm.set_value("custom_withholding_count", "");
			frm.set_value("custom_withholding_2", "");
			frm.set_value("custom_withholding_3", "");
		} else if (!frm.doc.custom_withholding_count) {
			frm.set_value("custom_withholding_count", "1");
		}
	},

	custom_withholding_count: function (frm) {
		const count = parseInt(frm.doc.custom_withholding_count) || 0;
		if (count < 2) frm.set_value("custom_withholding_3", "");
	},
});

// The manual withholding override (Ignore Withholding Treatment / Apply /
// Ignore) lives on the item row, not the invoice — the row is already where
// withholding base amounts and criteria (apply_tds, custom_is_service_item)
// are derived from, so the override belongs there too.
frappe.ui.form.on("Purchase Invoice Item", {
	custom_ignore_withholding_treatment: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.custom_ignore_withholding_treatment) {
			frappe.model.set_value(cdt, cdn, "custom_withholding_override_action", "");
			frappe.model.set_value(cdt, cdn, "custom_withholding_override_category", "");
		}
	},

	custom_withholding_override_action: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.custom_withholding_override_action) {
			frappe.model.set_value(cdt, cdn, "custom_withholding_override_category", "");
		}
	},
});

function _set_withholding_queries(frm) {
	["custom_withholding_2", "custom_withholding_3"].forEach(function (fieldname) {
		frm.set_query(fieldname, function () {
			const excluded = [];
			["custom_withholding_2", "custom_withholding_3"].forEach(function (f) {
				if (f !== fieldname && frm.doc[f]) excluded.push(frm.doc[f]);
			});
			return excluded.length ? { filters: [["name", "not in", excluded]] } : {};
		});
	});
}

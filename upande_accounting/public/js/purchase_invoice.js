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

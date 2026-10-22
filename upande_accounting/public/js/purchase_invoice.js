frappe.ui.form.on("Purchase Invoice", {
	refresh: function (frm) {
		_set_withholding_queries(frm);
		_add_cuin_scan_buttons(frm);
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

// Fill Control Unit Invoice Number from the QR code on a KRA ETR / eTIMS
// receipt. Most users are on desktops, so the phone option pairs their phone
// as a camera: it scans and the number is pushed back here over realtime.
function _add_cuin_scan_buttons(frm) {
	if (frm.doc.docstatus !== 0) return;

	const group = __("Scan CU Receipt");
	frm.add_custom_button(__("Using My Phone"), () => _scan_cuin_with_phone(frm), group);
	frm.add_custom_button(__("Using This Computer's Camera"), () => _scan_cuin_with_webcam(frm), group);
}

function _scan_cuin_with_webcam(frm) {
	new frappe.ui.Scanner({
		dialog: true,
		multiple: false,
		on_scan(data) {
			frappe
				.xcall("upande_accounting.cuin_scanner.parse_scan", { scanned_text: data.decodedText })
				.then((result) => _apply_cuin_scan(frm, result));
		},
	});
}

function _scan_cuin_with_phone(frm) {
	frappe.xcall("upande_accounting.cuin_scanner.create_scan_session").then((s) => {
		const handler = (data) => {
			if (!data || data.session !== s.session) return;
			dialog.hide();
			_apply_cuin_scan(frm, data);
		};

		const dialog = new frappe.ui.Dialog({
			title: __("Scan CU Receipt with Your Phone"),
			fields: [{ fieldtype: "HTML", fieldname: "qr" }],
			on_hide: () => frappe.realtime.off(s.event, handler),
		});

		dialog.fields_dict.qr.$wrapper.html(`
			<div class="text-center">
				<p>${__("Scan this code with your phone's camera, log in as yourself if asked, then scan the receipt's QR code.")}</p>
				<img src="data:image/svg+xml;base64,${s.qr_svg}" style="width: 220px; height: 220px;">
				<p class="small text-muted" style="word-break: break-all;">${frappe.utils.escape_html(s.url)}</p>
				<p class="text-muted">${__("Waiting for scan... This link expires in 15 minutes.")}</p>
			</div>
		`);

		frappe.realtime.on(s.event, handler);
		dialog.show();
	});
}

function _apply_cuin_scan(frm, result) {
	if (result.cuin) {
		frm.set_value("custom_control_unit_invoice_number", result.cuin);
		frappe.show_alert({ message: __("CU Invoice Number set to {0}", [result.cuin]), indicator: "green" });
		return;
	}

	// Unrecognised QR content: let the user type or pick the number out of it
	frappe.prompt(
		{
			fieldtype: "Data",
			fieldname: "cuin",
			label: __("Control Unit Invoice Number"),
			default: result.is_etims_link ? "" : result.raw,
			reqd: 1,
			description: result.is_etims_link
				? __("This is an eTIMS receipt. Its QR code only holds a KRA verification link, so type the CU Invoice No. printed on the receipt.")
				: __("The QR code didn't contain a recognisable CU Invoice Number. Edit the scanned text below."),
		},
		(values) => frm.set_value("custom_control_unit_invoice_number", values.cuin.trim()),
		__("Check Scanned Value")
	);
}

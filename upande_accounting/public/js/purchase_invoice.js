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
// as a camera: it scans and the number is pushed back here over realtime
// (or picked up by polling where realtime isn't available).
function _add_cuin_scan_buttons(frm) {
	if (frm.doc.docstatus !== 0) return;

	const group = __("Scan CU Receipt");
	frm.add_custom_button(__("Using My Phone"), () => _scan_cuin_with_phone(frm), group);
	frm.add_custom_button(__("Using This Computer's Camera"), () => _scan_cuin_with_webcam(frm), group);
}

const CUIN_FIELD = "custom_control_unit_invoice_number";

// Every scan replaces the field: it's cleared when scanning starts and on an
// invalid scan, and set again by the next valid one.
function _clear_cuin(frm) {
	if (frm.doc[CUIN_FIELD]) frm.set_value(CUIN_FIELD, "");
}

function _set_cuin(frm, cuin) {
	frm.set_value(CUIN_FIELD, cuin);
	frappe.show_alert({ message: __("CU Invoice Number set to {0}", [cuin]), indicator: "green" });
}

function _invalid_scan_message(result) {
	return result.is_etims_link
		? __("Couldn't get the CU Invoice Number from KRA for this eTIMS receipt. Scan again, or type the number printed on the receipt.")
		: __("That QR code isn't a KRA receipt code. Scan the QR code on the receipt again.");
}

function _type_cuin(frm) {
	frappe.prompt(
		{
			fieldtype: "Data",
			fieldname: "cuin",
			label: __("Control Unit Invoice Number"),
			reqd: 1,
			description: __("Type the CU Invoice No. printed on the receipt."),
		},
		(values) => _set_cuin(frm, values.cuin.trim()),
		__("Enter CU Invoice Number")
	);
}

function _set_scan_status($el, message, indicator) {
	$el.html(`<div class="alert alert-${indicator}" style="margin: 8px 0 0;">${frappe.utils.escape_html(message)}</div>`);
}

function _scan_cuin_with_webcam(frm) {
	_clear_cuin(frm);

	const dialog = new frappe.ui.Dialog({
		title: __("Scan CU Receipt"),
		fields: [{ fieldtype: "HTML", fieldname: "camera" }],
		secondary_action_label: __("Type It In"),
		secondary_action: () => {
			dialog.hide();
			_type_cuin(frm);
		},
	});
	const $wrapper = dialog.fields_dict.camera.$wrapper;
	// Closed dialogs stay in the DOM, so each reader needs its own id
	const reader_id = `cuin-webcam-reader-${frappe.utils.get_random(8)}`;
	$wrapper.html(`
		<p class="text-muted">${__("Hold the receipt's QR code inside the square, close enough to fill it but still sharp.")}</p>
		<div id="${reader_id}" style="width: 100%;"></div>
		<div class="cuin-scan-status"></div>
	`);
	const $status = $wrapper.find(".cuin-scan-status");

	let camera = null;
	dialog.onhide = () => camera && camera.stop();
	dialog.show();

	const start = () => {
		if (!dialog.display) return;
		upande_cuin_camera
			.start(document.getElementById(reader_id), on_text, () =>
				_set_scan_status(
					$status,
					__("Could not open the camera. Allow camera access for this site, or use your phone instead."),
					"danger"
				)
			)
			.then((c) => {
				camera = c;
				if (!dialog.display) camera.stop();
			});
	};

	const on_text = (text) => {
		_set_scan_status($status, __("Reading receipt..."), "info");
		frappe.xcall("upande_accounting.cuin_scanner.parse_scan", { scanned_text: text }).then((result) => {
			if (result.valid) {
				dialog.hide();
				_set_cuin(frm, result.cuin);
			} else {
				_set_scan_status($status, _invalid_scan_message(result), "warning");
				setTimeout(start, 1500);
			}
		});
	};

	frappe
		.require([
			"/assets/upande_accounting/js/lib/zxing-wasm/zxing_reader.iife.js",
			"/assets/upande_accounting/js/cuin_scan_camera.js",
		])
		.then(start);
}

function _get_phone_scan_session(frm) {
	// Reuse the phone link while it's still valid so a rescan doesn't need re-pairing
	const cached = frm.__cuin_phone_session;
	if (cached && cached.expires_at > Date.now() + 60 * 1000) return Promise.resolve(cached);

	return frappe.xcall("upande_accounting.cuin_scanner.create_scan_session").then((s) => {
		s.expires_at = Date.now() + 14 * 60 * 1000;
		frm.__cuin_phone_session = s;
		return s;
	});
}

function _scan_cuin_with_phone(frm) {
	_clear_cuin(frm);

	_get_phone_scan_session(frm).then((s) => {
		const seen = new Set();
		const handler = (data) => {
			if (!data || data.session !== s.session || seen.has(data.scan_id)) return;
			seen.add(data.scan_id);

			if (data.valid) {
				_set_cuin(frm, data.cuin);
				_set_scan_status(
					$status,
					__("CU Invoice Number set to {0}. Scanning another receipt on your phone will replace it.", [data.cuin]),
					"success"
				);
			} else {
				_clear_cuin(frm);
				_set_scan_status($status, _invalid_scan_message(data), "warning");
			}
		};

		// Realtime is the fast path; polling covers sites where socket.io isn't reachable
		const poll = setInterval(() => {
			frappe
				.xcall("upande_accounting.cuin_scanner.get_scan_result", { session: s.session })
				.then((data) => data && handler(data))
				.catch(() => {});
		}, 2000);

		const dialog = new frappe.ui.Dialog({
			title: __("Scan CU Receipt with Your Phone"),
			fields: [{ fieldtype: "HTML", fieldname: "qr" }],
			primary_action_label: __("Done"),
			primary_action: () => dialog.hide(),
			secondary_action_label: __("Type It In"),
			secondary_action: () => {
				dialog.hide();
				_type_cuin(frm);
			},
			on_hide: () => {
				clearInterval(poll);
				frappe.realtime.off(s.event, handler);
			},
		});

		dialog.fields_dict.qr.$wrapper.html(`
			<div class="text-center">
				<p>${__("Scan this code with your phone's camera, log in as yourself if asked, then scan the receipt's QR code.")}</p>
				<img src="data:image/svg+xml;base64,${s.qr_svg}" style="width: 220px; height: 220px;">
				<p class="small text-muted" style="word-break: break-all;">${frappe.utils.escape_html(s.url)}</p>
				<p class="text-muted">${__("Already have the scan page open on your phone? Just scan the receipt.")}</p>
				<div class="cuin-scan-status"></div>
			</div>
		`);
		const $status = dialog.fields_dict.qr.$wrapper.find(".cuin-scan-status");
		_set_scan_status($status, __("Waiting for a scan..."), "info");

		frappe.realtime.on(s.event, handler);
		dialog.show();
	});
}

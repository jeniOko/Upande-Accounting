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

	custom_control_unit_invoice_number: function (frm) {
		_parse_typed_cuin(frm);
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
// (or picked up by polling where realtime isn't available). The number can
// also be read from an image or PDF already attached to the invoice.
const CUIN_FIELD = "custom_control_unit_invoice_number";
const CUIN_BUTTONS = ["Using My Phone", "Using This Computer's Camera", "From an Attachment"];

function _add_cuin_scan_buttons(frm) {
	// Checked live on every load, so switching the feature off in Accounts
	// Settings applies at once, without a restart or fresh login
	frappe.xcall("upande_accounting.cuin_scanner.get_scanner_config").then((config) => {
		frm.__cuin_config = config;
		const group = __("Scan CU Receipt");
		// The form may have refreshed again while this was loading; never add twice
		CUIN_BUTTONS.forEach((label) => frm.remove_custom_button(__(label), group));
		if (!config.enabled || frm.doc.docstatus !== 0) return;

		frm.add_custom_button(__("Using My Phone"), () => _scan_cuin_with_phone(frm), group);
		frm.add_custom_button(__("Using This Computer's Camera"), () => _scan_cuin_with_webcam(frm), group);
		frm.add_custom_button(__("From an Attachment"), () => _read_cuin_from_attachment(frm), group);
		_style_cuin_scan_button(group);
	});
}

// Blue highlight that reads the same in light and dark mode. Targeted by the
// group's data-label (not a class) so it survives the toolbar being re-rendered.
function _style_cuin_scan_button(group) {
	if (document.getElementById("upande-cuin-scan-style")) return;
	const button = `.inner-group-button[data-label="${encodeURIComponent(group)}"] > .btn`;
	$(`<style id="upande-cuin-scan-style">
		${button},
		${button}:focus {
			background-color: var(--blue-600) !important;
			border-color: var(--blue-600) !important;
			color: #fff !important;
			--icon-stroke: #fff;
		}
		${button}:hover,
		${button}:active,
		.inner-group-button.show > .btn,
		${button}[aria-expanded="true"] {
			background-color: var(--blue-700) !important;
			border-color: var(--blue-700) !important;
			color: #fff !important;
		}
	</style>`).appendTo("head");
}

// Loads zxing and the camera module at their versioned URLs (production nginx
// caches /assets for a year, so unversioned URLs would keep serving old code)
function _load_cuin_scanner(frm) {
	const config = frm.__cuin_config;
	if (!config || !config.assets || !config.assets.camera) {
		return Promise.reject(new Error("CU scanner config not loaded"));
	}
	// Not frappe.require: it picks a loader by file extension, which a ?v= query
	// breaks, and it reports success even when a script fails to load
	return _load_script(config.assets.zxing)
		.then(() => _load_script(config.assets.camera))
		.then(() => {
			if (!window.upande_cuin_camera || !window.ZXingWASM) throw new Error("CU scanner scripts failed to load");
			upande_cuin_camera.configure(config.assets);
		});
}

const _loaded_scripts = {};

function _load_script(url) {
	if (!_loaded_scripts[url]) {
		_loaded_scripts[url] = new Promise((resolve, reject) => {
			const script = document.createElement("script");
			script.src = url;
			script.onload = resolve;
			script.onerror = () => {
				delete _loaded_scripts[url];
				reject(new Error(`Could not load ${url}`));
			};
			document.head.appendChild(script);
		});
	}
	return _loaded_scripts[url];
}

// Dialogs stay in the DOM after closing; remove them so every open starts fresh
function _destroy_dialog_after_hide(dialog) {
	setTimeout(() => dialog.$wrapper.remove(), 500);
}

// Bootstrap ignores hide() while the opening animation runs, and a cached
// scanner can read a code before it ends; wait for it so the dialog really closes
function _close_dialog(dialog) {
	if (dialog.display) {
		dialog.hide();
	} else {
		dialog.$wrapper.one("shown.bs.modal", () => dialog.hide());
	}
}

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

// A handheld USB / Bluetooth scanner types the QR code's full content (the
// KRA link) into the field, as does pasting it. Swap that for the CU invoice
// number; a number typed by hand is left exactly as entered.
function _parse_typed_cuin(frm) {
	const raw = (frm.doc[CUIN_FIELD] || "").trim();
	if (frm.__cuin_config && !frm.__cuin_config.enabled) return;
	if (!raw || !(raw.includes("://") || /\s/.test(raw))) return;

	frappe.call({
		method: "upande_accounting.cuin_scanner.parse_scan",
		args: { scanned_text: raw },
		freeze: true,
		freeze_message: __("Reading CU Invoice Number..."),
		callback: (r) => {
			// Ignore if the field was changed again while this was in flight
			if ((frm.doc[CUIN_FIELD] || "").trim() !== raw) return;
			const result = r.message || {};
			if (result.valid) {
				_set_cuin(frm, result.cuin);
			} else {
				frm.set_value(CUIN_FIELD, "");
				frappe.show_alert({ message: _invalid_scan_message(result), indicator: "orange" }, 8);
			}
		},
	});
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
	$wrapper.html(`
		<p class="text-muted">${__("Hold the receipt's QR code inside the square, close enough to fill it but still sharp.")}</p>
		<div class="cuin-webcam-reader" style="min-height: 160px; background: #000; border-radius: 8px;
			display: flex; align-items: center; justify-content: center; color: #fff; font-size: 13px;">
			${__("Loading scanner...")}
		</div>
		<div style="margin-top: 8px;">
			<button type="button" class="btn btn-default btn-sm cuin-upload-photo">${__("Upload Photo of Receipt")}</button>
			<input type="file" accept="image/*" class="cuin-photo-input" style="display: none;">
		</div>
		<div class="cuin-scan-status"></div>
	`);
	const $status = $wrapper.find(".cuin-scan-status");

	let scanner = null;
	// Not dialog.display: that only turns true once the opening animation ends,
	// so a fast (cached) script load would look like a closed dialog
	let closed = false;
	dialog.onhide = () => {
		closed = true;
		scanner && scanner.stop();
		_destroy_dialog_after_hide(dialog);
	};
	dialog.show();

	const scripts_ready = _load_cuin_scanner(frm);
	const load_failed = (err) => {
		console.error(err);
		$wrapper.find(".cuin-webcam-reader").text(__("Scanner failed to load."));
		_set_scan_status(
			$status,
			__("Couldn't load the scanner. Refresh the page (Ctrl+Shift+R) and try again, or use your phone instead."),
			"danger"
		);
	};

	const handle_text = (text) => {
		scanner && scanner.pause();
		_set_scan_status($status, __("Reading receipt..."), "info");
		frappe.xcall("upande_accounting.cuin_scanner.parse_scan", { scanned_text: text }).then((result) => {
			if (result.valid) {
				_close_dialog(dialog);
				_set_cuin(frm, result.cuin);
			} else {
				_set_scan_status($status, _invalid_scan_message(result), "warning");
				setTimeout(() => scanner && scanner.resume(), 1500);
			}
		});
	};

	$wrapper.find(".cuin-upload-photo").on("click", () => $wrapper.find(".cuin-photo-input").val("").trigger("click"));
	$wrapper.find(".cuin-photo-input").on("change", function () {
		const file = this.files && this.files[0];
		if (!file) return;
		scanner && scanner.pause();
		_set_scan_status($status, __("Reading photo..."), "info");
		scripts_ready
			.then(() => upande_cuin_camera.decode_file(file))
			.then((text) => {
				if (text) {
					handle_text(text);
				} else {
					_set_scan_status($status, __("No QR code found in the photo. Try a closer, sharper photo."), "warning");
					scanner && scanner.resume();
				}
			})
			.catch(load_failed);
	});

	scripts_ready
		.then(() => {
			if (closed) return;
			return upande_cuin_camera.open($wrapper.find(".cuin-webcam-reader")[0], {
				aspect: "4 / 3",
				on_text: handle_text,
				on_error: () =>
					_set_scan_status(
						$status,
						__("Could not open the camera. Allow camera access for this site, upload a photo, or use your phone instead."),
						"danger"
					),
			});
		})
		.then((s) => {
			scanner = s;
			if (scanner && closed) scanner.stop();
		})
		.catch(load_failed);
}

// A new phone link every time: never reuse an earlier session or dialog
function _scan_cuin_with_phone(frm) {
	_clear_cuin(frm);

	frappe.xcall("upande_accounting.cuin_scanner.create_scan_session").then((s) => {
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
				_destroy_dialog_after_hide(dialog);
			},
		});

		dialog.fields_dict.qr.$wrapper.html(`
			<div class="text-center">
				<p>${__("Scan this code with your phone's camera, log in as yourself if asked, then scan the receipt's QR code.")}</p>
				<img src="data:image/svg+xml;base64,${s.qr_svg}" style="width: 220px; height: 220px;">
				<p class="small text-muted" style="word-break: break-all;">${frappe.utils.escape_html(s.url)}</p>
				<div class="cuin-scan-status"></div>
			</div>
		`);
		const $status = dialog.fields_dict.qr.$wrapper.find(".cuin-scan-status");
		_set_scan_status($status, __("Waiting for a scan..."), "info");

		frappe.realtime.on(s.event, handler);
		dialog.show();
	});
}

const CUIN_ATTACHMENT_RE = /\.(pdf|png|jpe?g|webp|gif|bmp|heic|heif)$/i;

// Read the CU invoice number from an image or PDF attached to this invoice
function _read_cuin_from_attachment(frm) {
	const files = (frm.get_docinfo().attachments || []).filter((f) => CUIN_ATTACHMENT_RE.test(f.file_url || ""));
	if (!files.length) {
		frappe.msgprint(
			__("This invoice has no image or PDF attachments. Attach the receipt or invoice first, or scan it with your phone or camera.")
		);
		return;
	}
	if (files.length === 1) {
		_read_cuin_attachment(frm, files[0]);
		return;
	}

	frappe.prompt(
		{
			fieldtype: "Select",
			fieldname: "file_url",
			label: __("Attachment"),
			reqd: 1,
			options: files.map((f) => ({ label: f.file_name || f.file_url, value: f.file_url })),
			default: files[0].file_url,
		},
		(values) => _read_cuin_attachment(frm, files.find((f) => f.file_url === values.file_url)),
		__("Read CU Invoice Number from Attachment"),
		__("Read")
	);
}

function _read_cuin_attachment(frm, file) {
	const name = file.file_name || file.file_url;
	const is_pdf = /\.pdf$/i.test(file.file_url);
	_clear_cuin(frm);
	frappe.dom.freeze(__("Reading {0}...", [name]));

	// PDFs: the text and links first (fast, server side), then render the pages
	// and look for the QR code; images: decode the QR code in the browser
	const from_text = is_pdf
		? frappe.xcall("upande_accounting.cuin_scanner.read_attachment_cuin", {
				doctype: frm.doctype,
				docname: frm.doc.name,
				file_url: file.file_url,
		  })
		: Promise.resolve(null);

	from_text
		.then((result) => {
			if (result && result.valid) return result;
			return _load_cuin_scanner(frm)
				.then(() => fetch(file.file_url, { credentials: "same-origin" }))
				.then((response) => {
					if (!response.ok) throw new Error(`Could not download ${file.file_url}: ${response.status}`);
					return is_pdf ? response.arrayBuffer() : response.blob();
				})
				.then((data) => (is_pdf ? upande_cuin_camera.decode_pdf(data) : upande_cuin_camera.decode_file(data)))
				.then((text) =>
					text ? frappe.xcall("upande_accounting.cuin_scanner.parse_scan", { scanned_text: text }) : null
				);
		})
		.then((result) => {
			frappe.dom.unfreeze();
			if (result && result.valid) {
				_set_cuin(frm, result.cuin);
			} else if (result) {
				frappe.msgprint(_invalid_scan_message(result));
			} else {
				frappe.msgprint(__("No KRA receipt QR code or CU Invoice Number was found in {0}.", [name]));
			}
		})
		.catch((err) => {
			frappe.dom.unfreeze();
			console.error(err);
			frappe.msgprint(__("Couldn't read {0}. Try another attachment, or scan the receipt with your phone.", [name]));
		});
}

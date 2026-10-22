// Camera QR reader tuned for the small QR codes on KRA ETR / eTIMS receipts.
// Shared by the Purchase Invoice webcam dialog and the /cuin-scan phone page;
// needs html5-qrcode loaded first.
window.upande_cuin_camera = {
	// Starts scanning into `container` (a DOM element with an id) and calls
	// on_text(decoded_text) once. Returns a promise of { stop() }.
	start(container, on_text, on_error) {
		/* global Html5Qrcode, Html5QrcodeSupportedFormats */
		const reader = new Html5Qrcode(container.id, {
			formatsToSupport: [Html5QrcodeSupportedFormats.QR_CODE],
			// Native detector (Chrome / Android) is much faster than the JS decoder
			experimentalFeatures: { useBarCodeDetectorIfSupported: true },
			verbose: false,
		});
		let done = false;

		const stop = () => {
			$(container).siblings(".cuin-camera-controls").remove();
			return reader.isScanning ? reader.stop().catch(() => {}) : Promise.resolve();
		};

		return reader
			.start(
				{ facingMode: "environment" },
				{
					fps: 20,
					disableFlip: true,
					// Scan most of the frame so a small code doesn't need lining up in a box
					qrbox: (w, h) => {
						const size = Math.max(160, Math.floor(Math.min(w, h) * 0.9));
						return { width: size, height: size };
					},
					// High resolution keeps a small code legible from a comfortable distance
					videoConstraints: {
						facingMode: "environment",
						width: { ideal: 1920 },
						height: { ideal: 1080 },
						advanced: [{ focusMode: "continuous" }],
					},
				},
				(text) => {
					if (done) return;
					done = true;
					if (navigator.vibrate) navigator.vibrate(80);
					stop().then(() => on_text(text));
				},
				() => {}
			)
			.then(() => {
				this._add_camera_controls(container, reader);
				return { stop };
			})
			.catch((err) => {
				console.error(err);
				on_error && on_error(err);
				return { stop };
			});
	},

	// Flashlight and zoom buttons, only where the camera supports them
	_add_camera_controls(container, reader) {
		$(container).siblings(".cuin-camera-controls").remove();
		let caps = {};
		try {
			caps = reader.getRunningTrackCapabilities() || {};
		} catch (e) {
			return;
		}

		const $bar = $(`<div class="cuin-camera-controls" style="display:flex; gap:8px; margin-top:8px;"></div>`);

		if (caps.torch) {
			let torch = false;
			$(`<button type="button" class="btn btn-default btn-sm">${__("Flashlight")}</button>`)
				.on("click", function () {
					torch = !torch;
					reader.applyVideoConstraints({ advanced: [{ torch }] }).catch(() => {});
					$(this).toggleClass("btn-primary", torch);
				})
				.appendTo($bar);
		}

		if (caps.zoom && caps.zoom.max > 1) {
			const steps = [1, 2, 3].filter((z) => z >= caps.zoom.min && z <= caps.zoom.max);
			let i = 0;
			$(`<button type="button" class="btn btn-default btn-sm">${__("Zoom")} 1x</button>`)
				.on("click", function () {
					i = (i + 1) % steps.length;
					reader.applyVideoConstraints({ advanced: [{ zoom: steps[i] }] }).catch(() => {});
					$(this).text(`${__("Zoom")} ${steps[i]}x`);
				})
				.appendTo($bar);
		}

		if ($bar.children().length) $(container).after($bar);
	},
};

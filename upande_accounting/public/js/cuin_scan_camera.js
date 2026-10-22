// Camera QR reader for the tiny (~2cm) QR codes on KRA ETR / eTIMS receipts.
// Shared by the Purchase Invoice webcam dialog and the /cuin-scan phone page.
//
// Generic scanners shrink each frame to the on-screen preview size before
// decoding, which leaves too few pixels per QR module for a small code. This
// reads full-resolution frames instead and alternates two regions: the whole
// frame (big codes) and an enlarged centre crop (tiny codes held in the guide
// square). Decoding uses the browser's native BarcodeDetector where available
// and zxing-cpp (WebAssembly) everywhere, which copes with rotated and tilted codes.
//
// Load ZXING_SCRIPT before this file.
window.upande_cuin_camera = {
	ZXING_SCRIPT: "/assets/upande_accounting/js/lib/zxing-wasm/zxing_reader.iife.js",
	ZXING_WASM: "/assets/upande_accounting/js/lib/zxing-wasm/zxing_reader.wasm",

	// Centre crop as a share of the frame's shorter side; also the guide square size
	CROP: 0.5,
	// Longest side handed to a decoder, to keep each attempt fast
	MAX_DECODE_SIDE: 1600,
	ZXING_OPTIONS: {
		formats: ["QRCode"],
		tryHarder: true,
		tryRotate: true,
		tryInvert: false,
		tryDownscale: true,
		maxNumberOfSymbols: 1,
	},

	// Starts scanning into `container` and calls on_text(decoded_text) once.
	// Returns a promise of { stop() }.
	start(container, on_text, on_error) {
		const state = { stopped: false, stream: null, timer: null };
		const $container = $(container).empty().css({ position: "relative", overflow: "hidden", borderRadius: "8px" });
		const video = $(
			`<video playsinline muted autoplay style="width: 100%; display: block; background: #000;"></video>`
		).appendTo($container)[0];
		const $guide = $(`<div style="position: absolute; left: 50%; top: 50%; width: 50%; aspect-ratio: 1;
			transform: translate(-50%, -50%); border: 3px solid rgba(255, 255, 255, 0.9); border-radius: 12px;
			box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.3); pointer-events: none;"></div>`).appendTo($container);

		const stop = () => {
			state.stopped = true;
			clearTimeout(state.timer);
			if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
			video.srcObject = null;
			$container.siblings(".cuin-camera-controls").remove();
			return Promise.resolve();
		};
		const controller = { stop };

		const fail = (err) => {
			console.error(err);
			stop();
			on_error && on_error(err);
			return controller;
		};

		if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
			return Promise.resolve(fail(new Error("Camera not available (the page must be opened over https)")));
		}

		return navigator.mediaDevices
			.getUserMedia({
				audio: false,
				video: {
					facingMode: { ideal: "environment" },
					width: { ideal: 3840 },
					height: { ideal: 2160 },
				},
			})
			.then((stream) => {
				state.stream = stream;
				if (state.stopped) return stop().then(() => []);

				video.srcObject = stream;
				return video.play().then(() => this._make_decoders());
			})
			.then((decoders) => {
				if (state.stopped) return controller;
				if (!decoders.length) return fail(new Error("No QR decoder available"));

				const track = state.stream.getVideoTracks()[0];
				this._tune_camera(track);
				this._add_camera_controls(container, track);
				if ((track.getSettings().facingMode || "") === "user") {
					$(video).css("transform", "scaleX(-1)");
				}

				const vw = video.videoWidth;
				const vh = video.videoHeight;
				$guide.css("width", `${((Math.min(vw, vh) * this.CROP) / vw) * 100}%`);

				this._scan_loop(video, decoders, state, (text) => {
					stop();
					if (navigator.vibrate) navigator.vibrate(80);
					on_text(text);
				});
				return controller;
			})
			.catch(fail);
	},

	_make_decoders() {
		const decoders = [];
		const add_zxing = () => {
			if (!window.ZXingWASM) return;
			if (!this._zxing_ready) {
				ZXingWASM.prepareZXingModule({
					overrides: {
						locateFile: (path, prefix) => (path.endsWith(".wasm") ? this.ZXING_WASM : prefix + path),
					},
				});
				this._zxing_ready = true;
			}
			decoders.push((canvas, ctx) =>
				ZXingWASM.readBarcodes(ctx.getImageData(0, 0, canvas.width, canvas.height), this.ZXING_OPTIONS).then(
					(results) => {
						const hit = results.find((r) => r.isValid);
						return hit && hit.text;
					}
				)
			);
		};

		if (!("BarcodeDetector" in window)) {
			add_zxing();
			return Promise.resolve(decoders);
		}

		/* global BarcodeDetector */
		return BarcodeDetector.getSupportedFormats()
			.then((formats) => {
				if (formats.includes("qr_code")) {
					const detector = new BarcodeDetector({ formats: ["qr_code"] });
					decoders.push((canvas) => detector.detect(canvas).then((r) => r[0] && r[0].rawValue));
				}
			})
			.catch(() => {})
			.then(() => {
				add_zxing();
				return decoders;
			});
	},

	_scan_loop(video, decoders, state, on_found) {
		const canvas = document.createElement("canvas");
		const ctx = canvas.getContext("2d", { willReadFrequently: true });
		const regions = [this._full_frame_region.bind(this), this._centre_crop_region.bind(this)];
		const attempts = [];
		regions.forEach((region) => decoders.forEach((decode) => attempts.push({ region, decode })));
		let i = 0;

		const tick = () => {
			if (state.stopped) return;
			if (video.readyState < 2 || !video.videoWidth) {
				state.timer = setTimeout(tick, 100);
				return;
			}

			const { region, decode } = attempts[i++ % attempts.length];
			const r = region(video.videoWidth, video.videoHeight);
			canvas.width = r.dw;
			canvas.height = r.dh;
			ctx.imageSmoothingEnabled = true;
			ctx.drawImage(video, r.sx, r.sy, r.sw, r.sh, 0, 0, r.dw, r.dh);

			Promise.resolve()
				.then(() => decode(canvas, ctx))
				.catch((err) => {
					console.error(err);
					return null;
				})
				.then((text) => {
					if (state.stopped) return;
					if (text) {
						on_found(text);
					} else {
						state.timer = setTimeout(tick, 20);
					}
				});
		};
		tick();
	},

	_full_frame_region(vw, vh) {
		const scale = Math.min(1, this.MAX_DECODE_SIDE / Math.max(vw, vh));
		return { sx: 0, sy: 0, sw: vw, sh: vh, dw: Math.round(vw * scale), dh: Math.round(vh * scale) };
	},

	// Enlarged so a tiny code gets enough pixels per module for the decoder
	_centre_crop_region(vw, vh) {
		const side = Math.round(Math.min(vw, vh) * this.CROP);
		const out = Math.min(this.MAX_DECODE_SIDE, side * 2);
		return { sx: Math.round((vw - side) / 2), sy: Math.round((vh - side) / 2), sw: side, sh: side, dw: out, dh: out };
	},

	_capabilities(track) {
		try {
			return (track.getCapabilities && track.getCapabilities()) || {};
		} catch (e) {
			return {};
		}
	},

	// Continuous focus, and 2x zoom so a small code can be held at a distance
	// the camera can actually focus at
	_tune_camera(track) {
		const caps = this._capabilities(track);
		const advanced = [];
		if ((caps.focusMode || []).includes("continuous")) advanced.push({ focusMode: "continuous" });
		if (caps.zoom && caps.zoom.max >= 2) advanced.push({ zoom: 2 });
		if (advanced.length) track.applyConstraints({ advanced }).catch(() => {});
	},

	// Flashlight and zoom buttons, only where the camera supports them
	_add_camera_controls(container, track) {
		$(container).siblings(".cuin-camera-controls").remove();
		const caps = this._capabilities(track);
		const $bar = $(`<div class="cuin-camera-controls" style="display: flex; gap: 8px; margin-top: 8px;"></div>`);

		if (caps.torch) {
			let torch = false;
			$(`<button type="button" class="btn btn-default btn-sm">${__("Flashlight")}</button>`)
				.on("click", function () {
					torch = !torch;
					track.applyConstraints({ advanced: [{ torch }] }).catch(() => {});
					$(this).toggleClass("btn-primary", torch);
				})
				.appendTo($bar);
		}

		if (caps.zoom && caps.zoom.max > 1) {
			const steps = [1, 2, 3, 4].filter((z) => z >= caps.zoom.min && z <= caps.zoom.max);
			let i = caps.zoom.max >= 2 ? Math.max(0, steps.indexOf(2)) : 0;
			$(`<button type="button" class="btn btn-default btn-sm">${__("Zoom")} ${steps[i]}x</button>`)
				.on("click", function () {
					i = (i + 1) % steps.length;
					track.applyConstraints({ advanced: [{ zoom: steps[i] }] }).catch(() => {});
					$(this).text(`${__("Zoom")} ${steps[i]}x`);
				})
				.appendTo($bar);
		}

		if ($bar.children().length) $(container).after($bar);
	},
};

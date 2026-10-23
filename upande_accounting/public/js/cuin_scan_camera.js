// Camera QR reader for the tiny (~2cm) QR codes on KRA ETR / eTIMS receipts.
// Shared by the Purchase Invoice webcam dialog and the /cuin-scan phone page.
//
// A 2cm KRA code has ~0.5mm modules, so what decides a read is pixels on the
// code and focus, not the decoder. So this:
// - reads full-resolution frames (generic scanners decode the shrunk preview),
//   alternating the whole frame with an enlarged centre crop (the guide square);
// - defaults to 2x zoom: on phones that crops the sensor, which adds real detail
//   and lets the phone sit far enough away to focus;
// - uses the browser's BarcodeDetector where it exists (Chrome on Android uses
//   Google's ML Kit, the engine behind Google Lens) alongside zxing-cpp
//   (WebAssembly), which copes with rotated, tilted and faded codes;
// - keeps one camera stream open and only pauses decoding between scans, and
//   reopens the camera if the phone drops it (screen lock, app switch);
// - can decode a still photo (decode_file), which gets the phone camera app's
//   full resolution, proper focus and macro mode for the smallest codes.
//
// Load ZXING_SCRIPT before this file.
window.upande_cuin_camera = {
	ZXING_SCRIPT: "/assets/upande_accounting/js/lib/zxing-wasm/zxing_reader.iife.js",
	ZXING_WASM: "/assets/upande_accounting/js/lib/zxing-wasm/zxing_reader.wasm",

	// Centre crop as a share of the frame's shorter side; also the guide square size
	CROP: 0.5,
	// Longest side handed to a decoder per live attempt, to keep each one fast
	MAX_DECODE_SIDE: 1600,
	// Longest side a still photo is decoded at
	PHOTO_MAX_SIDE: 4000,
	DEFAULT_ZOOM: 2,
	ZXING_OPTIONS: {
		formats: ["QRCode"],
		tryHarder: true,
		tryRotate: true,
		tryInvert: false,
		tryDownscale: true,
		maxNumberOfSymbols: 1,
	},

	_active: null,

	// Opens the camera in `container` and scans continuously. On each read it
	// pauses and calls on_text(text); call resume() to scan again.
	// Returns a promise of { pause(), resume(), stop() }.
	open(container, { on_text, on_error, aspect = "3 / 4" }) {
		if (this._active) this._active.stop();

		const state = { stopped: false, paused: false, stream: null, track: null, timer: null, restarting: null };
		const $container = $(container)
			.empty()
			.css({
				position: "relative",
				width: "100%",
				aspectRatio: aspect,
				maxHeight: "65vh",
				overflow: "hidden",
				borderRadius: "8px",
				background: "#000",
			});
		$container.siblings(".cuin-camera-controls").remove();

		const video = $(`<video playsinline muted autoplay
			style="position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover;"></video>`).appendTo(
			$container
		)[0];
		const $guide = $(`<div style="position: absolute; left: 50%; top: 50%; width: 50%; aspect-ratio: 1;
			transform: translate(-50%, -50%); border: 3px solid rgba(255, 255, 255, 0.9); border-radius: 12px;
			box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.3); pointer-events: none;"></div>`).appendTo($container);
		const $controls = $(
			`<div class="cuin-camera-controls" style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px;"></div>`
		).insertAfter($container);

		// Never leave a blank box: say what's happening until the picture appears.
		// A pending or dismissed browser permission prompt otherwise looks like nothing.
		const $notice = $(`<div style="position: absolute; inset: 0; display: flex; align-items: center;
			justify-content: center; padding: 16px; text-align: center; color: #fff; font-size: 13px;"></div>`)
			.text(__("Starting camera... If your browser asks, allow camera access."))
			.appendTo($container);
		const slow_notice = setTimeout(() => {
			if (!video.videoWidth) {
				$notice.text(
					__("Still waiting for the camera. Allow camera access for this site (camera icon in the address bar), or use a photo instead.")
				);
			}
		}, 6000);
		video.addEventListener("playing", () => {
			clearTimeout(slow_notice);
			$notice.hide();
		});

		// Size the guide to the centre crop as it appears in the object-fit: cover preview
		const layout_guide = () => {
			const vw = video.videoWidth;
			const vh = video.videoHeight;
			if (!vw || !vh) return;
			const scale = Math.max($container.width() / vw, $container.height() / vh);
			const side = Math.min(vw, vh) * this.CROP * scale;
			$guide.css({ width: `${side}px`, height: `${side}px` });
		};

		const release = () => {
			if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
			state.stream = null;
			state.track = null;
		};

		const start_stream = () =>
			navigator.mediaDevices
				.getUserMedia({
					audio: false,
					video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } },
				})
				.then((stream) => {
					if (state.stopped) {
						stream.getTracks().forEach((t) => t.stop());
						return;
					}
					state.stream = stream;
					state.track = stream.getVideoTracks()[0];
					state.track.addEventListener("ended", restart);
					video.srcObject = stream;
					if ((state.track.getSettings().facingMode || "") === "user") {
						$(video).css("transform", "scaleX(-1)");
					}
					return video.play().then(() => {
						this._tune_camera(state.track);
						this._render_controls($controls, state.track);
						layout_guide();
					});
				});

		// Phones drop the stream on screen lock or app switch; reopen it instead of
		// leaving a blank preview
		const restart = () => {
			if (state.stopped || state.restarting) return;
			release();
			state.restarting = start_stream()
				.catch(fail)
				.finally(() => (state.restarting = null));
		};

		const on_visibility = () => {
			if (document.visibilityState !== "visible" || state.stopped) return;
			if (!state.track || state.track.readyState === "ended") {
				restart();
			} else {
				video.play().catch(() => {});
			}
		};

		const on_resize = () => layout_guide();

		const scanner = {
			pause: () => (state.paused = true),
			resume: () => {
				state.paused = false;
				if (state.track && state.track.readyState === "ended") restart();
			},
			stop: () => {
				state.stopped = true;
				clearTimeout(state.timer);
				clearTimeout(slow_notice);
				release();
				video.srcObject = null;
				$controls.remove();
				document.removeEventListener("visibilitychange", on_visibility);
				window.removeEventListener("resize", on_resize);
				if (this._active === scanner) this._active = null;
			},
		};

		const fail = (err) => {
			console.error(err);
			clearTimeout(slow_notice);
			$notice.show().text(__("Camera unavailable. Allow camera access for this site, or use a photo instead."));
			on_error && on_error(err);
		};

		this._active = scanner;
		document.addEventListener("visibilitychange", on_visibility);
		window.addEventListener("resize", on_resize);
		video.addEventListener("loadedmetadata", layout_guide);
		// Tap the preview to refocus, where the camera allows it
		video.addEventListener("click", () => this._refocus(state.track));

		if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
			fail(new Error("Camera not available (the page must be opened over https)"));
			return Promise.resolve(scanner);
		}

		return start_stream()
			.then(() => this._decoders())
			.then((decoders) => {
				if (state.stopped) return scanner;
				if (!decoders.zxing && !decoders.native) throw new Error("No QR decoder available");

				this._scan_loop(video, decoders, state, (text) => {
					state.paused = true;
					if (navigator.vibrate) navigator.vibrate(80);
					on_text(text);
				});
				return scanner;
			})
			.catch((err) => {
				fail(err);
				return scanner;
			});
	},

	// Reads the QR code from a photo (File / Blob). Resolves to the text or null.
	decode_file(file) {
		return this._decoders().then((decoders) => {
			// zxing decodes JPEG / PNG itself at full resolution
			const direct = decoders.zxing
				? ZXingWASM.readBarcodes(file, this.ZXING_OPTIONS)
						.then((results) => this._first_valid(results))
						.catch(() => null)
				: Promise.resolve(null);

			return direct.then((text) => {
				if (text) return text;
				// Formats zxing can't open (e.g. HEIC) go through the browser instead
				return createImageBitmap(file)
					.then((bitmap) => {
						const scale = Math.min(1, this.PHOTO_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
						const canvas = document.createElement("canvas");
						canvas.width = Math.round(bitmap.width * scale);
						canvas.height = Math.round(bitmap.height * scale);
						const ctx = canvas.getContext("2d", { willReadFrequently: true });
						ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
						const native = decoders.native
							? decoders.native.detect(canvas).then((r) => r[0] && r[0].rawValue)
							: Promise.resolve(null);
						return native.then(
							(found) =>
								found ||
								(decoders.zxing
									? ZXingWASM.readBarcodes(
											ctx.getImageData(0, 0, canvas.width, canvas.height),
											this.ZXING_OPTIONS
									  ).then((results) => this._first_valid(results))
									: null)
						);
					})
					.catch((err) => {
						console.error(err);
						return null;
					});
			});
		});
	},

	_first_valid(results) {
		const hit = (results || []).find((r) => r.isValid);
		return hit ? hit.text : null;
	},

	// { native: BarcodeDetector | null, zxing: bool }, created once per page
	_decoders() {
		if (this._decoders_promise) return this._decoders_promise;

		const zxing = !!window.ZXingWASM;
		if (zxing) {
			ZXingWASM.prepareZXingModule({
				overrides: {
					locateFile: (path, prefix) => (path.endsWith(".wasm") ? this.ZXING_WASM : prefix + path),
				},
			});
		}

		/* global BarcodeDetector */
		const native =
			"BarcodeDetector" in window
				? BarcodeDetector.getSupportedFormats()
						.then((formats) =>
							formats.includes("qr_code") ? new BarcodeDetector({ formats: ["qr_code"] }) : null
						)
						.catch(() => null)
				: Promise.resolve(null);

		this._decoders_promise = native.then((detector) => ({ native: detector, zxing }));
		return this._decoders_promise;
	},

	_scan_loop(video, decoders, state, on_found) {
		const canvas = document.createElement("canvas");
		const ctx = canvas.getContext("2d", { willReadFrequently: true });
		const full = this._full_frame_region.bind(this);
		const centre = this._centre_crop_region.bind(this);

		const native = (region) => () =>
			region
				? decoders.native.detect(this._draw(video, canvas, ctx, region)).then((r) => r[0] && r[0].rawValue)
				: decoders.native.detect(video).then((r) => r[0] && r[0].rawValue);
		const zxing = (region) => () => {
			this._draw(video, canvas, ctx, region);
			return ZXingWASM.readBarcodes(ctx.getImageData(0, 0, canvas.width, canvas.height), this.ZXING_OPTIONS).then(
				(results) => this._first_valid(results)
			);
		};

		// Native detector first and most often: it is the fastest where it exists
		const attempts = [];
		if (decoders.native) attempts.push(native(null));
		if (decoders.zxing) attempts.push(zxing(centre));
		if (decoders.native) attempts.push(native(centre));
		if (decoders.zxing) attempts.push(zxing(full));
		let i = 0;

		const tick = () => {
			if (state.stopped) return;
			if (state.paused || video.readyState < 2 || !video.videoWidth) {
				state.timer = setTimeout(tick, 120);
				return;
			}

			Promise.resolve()
				.then(attempts[i++ % attempts.length])
				.catch((err) => {
					console.error(err);
					return null;
				})
				.then((text) => {
					if (state.stopped) return;
					if (text && !state.paused) on_found(text);
					state.timer = setTimeout(tick, 15);
				});
		};
		tick();
	},

	_draw(video, canvas, ctx, region) {
		const r = region(video.videoWidth, video.videoHeight);
		canvas.width = r.dw;
		canvas.height = r.dh;
		ctx.imageSmoothingEnabled = true;
		ctx.drawImage(video, r.sx, r.sy, r.sw, r.sh, 0, 0, r.dw, r.dh);
		return canvas;
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
			return (track && track.getCapabilities && track.getCapabilities()) || {};
		} catch (e) {
			return {};
		}
	},

	_tune_camera(track) {
		const caps = this._capabilities(track);
		const advanced = [];
		if ((caps.focusMode || []).includes("continuous")) advanced.push({ focusMode: "continuous" });
		if (caps.zoom && caps.zoom.max >= this.DEFAULT_ZOOM) advanced.push({ zoom: this.DEFAULT_ZOOM });
		if (advanced.length) track.applyConstraints({ advanced }).catch(() => {});
	},

	_refocus(track) {
		const caps = this._capabilities(track);
		if (!(caps.focusMode || []).includes("single-shot")) return;
		track
			.applyConstraints({ advanced: [{ focusMode: "single-shot" }] })
			.then(() =>
				setTimeout(() => track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }).catch(() => {}), 1500)
			)
			.catch(() => {});
	},

	// Zoom 1x / 2x / 3x and flashlight, only where the camera supports them
	_render_controls($controls, track) {
		$controls.empty();
		const caps = this._capabilities(track);

		if (caps.zoom && caps.zoom.max > 1) {
			const steps = [1, 2, 3].filter((z) => z >= caps.zoom.min && z <= caps.zoom.max);
			const current = Math.round((track.getSettings && track.getSettings().zoom) || this.DEFAULT_ZOOM);
			const $group = $(`<div class="btn-group" role="group"></div>`).appendTo($controls);
			steps.forEach((z) => {
				$(`<button type="button" class="btn btn-sm ${z === current ? "btn-primary" : "btn-default"}">${z}x</button>`)
					.on("click", function () {
						track.applyConstraints({ advanced: [{ zoom: z }] }).catch(() => {});
						$group.find(".btn").removeClass("btn-primary").addClass("btn-default");
						$(this).removeClass("btn-default").addClass("btn-primary");
					})
					.appendTo($group);
			});
		}

		if (caps.torch) {
			let torch = false;
			$(`<button type="button" class="btn btn-default btn-sm">${__("Flashlight")}</button>`)
				.on("click", function () {
					torch = !torch;
					track.applyConstraints({ advanced: [{ torch }] }).catch(() => {});
					$(this).toggleClass("btn-primary", torch).toggleClass("btn-default", !torch);
				})
				.appendTo($controls);
		}
	},
};

"""Scan a KRA ETR / eTIMS receipt QR code into a Control Unit Invoice Number.

Two ways in:
- The desk form scans with the computer's own webcam and calls `parse_scan`.
- The desk form opens a short-lived scan session and shows a QR code; the user
  opens it on their phone (/cuin-scan), scans the receipt there, and the result
  is pushed back to the desk form over realtime for the same user. The result
  is also kept in cache so the desk form can poll for it where realtime is down.

- The desk form reads an image or PDF already attached to the invoice.

TIMS receipts carry the CU invoice number in the QR link itself. eTIMS receipts
only carry a KRA verification link, so the number is read off that KRA page.

The whole feature can be switched off in Accounts Settings (Invoice and Billing).
"""

import hashlib
import io
import os
import re
from urllib.parse import parse_qsl, urlparse

import requests

import frappe
from frappe import _
from frappe.twofactor import get_qr_svg_code
from frappe.utils import get_url

SESSION_TTL = 15 * 60
REALTIME_EVENT = "cuin_scanned"

# eTIMS: KRACU0100000123/45 ; TIMS: 19-digit CU serial + invoice number
_ETIMS_RE = re.compile(r"\b(KRACU[0-9A-Z]+/\d+)\b", re.IGNORECASE)
_VALID_CUIN_RE = re.compile(r"^(\d{17,20}|KRACU[0-9A-Z]+/\d+)$", re.IGNORECASE)
_INVOICE_PARAMS = ("invoiceno", "invoice_no", "cuin", "cuinvoiceno", "cu_invoice_no", "invno")
# Only KRA's own eTIMS hosts are ever fetched, never an arbitrary scanned URL
_ETIMS_HOSTS = ("etims.kra.go.ke", "etims-sbx.kra.go.ke")
_ETIMS_LOOKUP_TIMEOUT = 10
SETTING_FIELD = "custom_enable_cu_invoice_scanning"


def is_scanning_enabled():
	# Off until turned on in Accounts Settings (never saved reads as 0)
	return bool(frappe.utils.cint(frappe.db.get_single_value("Accounts Settings", SETTING_FIELD)))


def _check_enabled():
	if not is_scanning_enabled():
		frappe.throw(_("CU Invoice Number scanning is turned off in Accounts Settings."))


# Production nginx caches /assets for a year, so every scanner asset URL carries
# a content hash: a deploy is picked up at once instead of browsers running stale code.
_SCANNER_ASSETS = {
	"zxing": "lib/zxing-wasm/zxing_reader.iife.js",
	"zxing_wasm": "lib/zxing-wasm/zxing_reader.wasm",
	"camera": "cuin_scan_camera.js",
	"pdfjs": "lib/pdfjs/pdf.min.js",
	"pdfjs_worker": "lib/pdfjs/pdf.worker.min.js",
}
_asset_versions = {}


def _asset_url(path):
	full_path = frappe.get_app_path("upande_accounting", "public", "js", path)
	mtime = os.path.getmtime(full_path)
	cached = _asset_versions.get(full_path)
	if not cached or cached[0] != mtime:
		with open(full_path, "rb") as f:
			cached = (mtime, hashlib.md5(f.read()).hexdigest()[:10])
		_asset_versions[full_path] = cached
	return f"/assets/upande_accounting/js/{path}?v={cached[1]}"


def scanner_assets():
	return {key: _asset_url(path) for key, path in _SCANNER_ASSETS.items()}


@frappe.whitelist()
def get_scanner_config():
	"""Read on every form load, so switching the feature off applies straight away."""
	enabled = is_scanning_enabled()
	return {"enabled": enabled, "assets": scanner_assets() if enabled else {}}


def _cache_key(session):
	return f"upande_accounting:cuin_scan:{session}"


def _result_key(session):
	return f"upande_accounting:cuin_scan_result:{session}"


def is_valid_cuin(value):
	return bool(_VALID_CUIN_RE.match((value or "").strip()))


def parse_cuin(text):
	"""Return the CU invoice number in scanned QR text, or None if it isn't a KRA receipt code."""
	text = (text or "").strip()
	if not text:
		return None

	parsed = urlparse(text)
	if parsed.scheme in ("http", "https"):
		# TIMS: https://itax.kra.go.ke/KRA-Portal/invoiceChk.htm?...&invoiceNo=<number>
		if not (parsed.hostname or "").lower().endswith("kra.go.ke"):
			return None
		for key, value in parse_qsl(parsed.query):
			if key.lower() in _INVOICE_PARAMS and is_valid_cuin(value):
				return value.strip().upper()
		return None

	if is_valid_cuin(text):
		return text.upper()

	match = _ETIMS_RE.search(text)
	return match.group(1).upper() if match else None


def is_etims_link(text):
	# eTIMS QR codes only carry a verification link (PIN + branch + receipt
	# signature); the CU invoice number is shown on the KRA page it opens.
	parsed = urlparse((text or "").strip())
	return parsed.scheme == "https" and (parsed.hostname or "").lower() in _ETIMS_HOSTS


def lookup_etims_cuin(url):
	"""Read the CU invoice number off KRA's eTIMS receipt verification page."""
	if not is_etims_link(url):
		return None
	try:
		response = requests.get(
			url.strip(), timeout=_ETIMS_LOOKUP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}
		)
		response.raise_for_status()
	except requests.RequestException:
		frappe.log_error(title="eTIMS CU invoice number lookup failed", message=frappe.get_traceback())
		return None

	match = _ETIMS_RE.search(response.text)
	return match.group(1).upper() if match else None


@frappe.whitelist()
def parse_scan(scanned_text, manual=0):
	"""`valid` is False when the QR isn't a KRA receipt code (or KRA couldn't be
	reached for an eTIMS one), so the scanner should try again."""
	_check_enabled()
	etims = is_etims_link(scanned_text)
	if frappe.utils.cint(manual):
		# Typed by the user from the printed receipt: trust it as entered
		cuin = (scanned_text or "").strip()[:140] or None
	elif etims:
		cuin = lookup_etims_cuin(scanned_text)
	else:
		cuin = parse_cuin(scanned_text)
	return {
		"scan_id": frappe.generate_hash(length=10),
		"cuin": cuin,
		"valid": bool(cuin),
		"raw": scanned_text,
		"is_etims_link": etims,
	}


@frappe.whitelist()
def create_scan_session():
	_check_enabled()
	session = frappe.generate_hash(length=20)
	frappe.cache.set_value(_cache_key(session), frappe.session.user, expires_in_sec=SESSION_TTL)

	url = get_url(f"/cuin-scan?session={session}")
	return {
		"session": session,
		"url": url,
		"qr_svg": get_qr_svg_code(url).decode(),
		"event": REALTIME_EVENT,
	}


@frappe.whitelist()
def submit_scan(session, scanned_text, manual=0):
	_check_enabled()
	owner = frappe.cache.get_value(_cache_key(session))
	if not owner:
		frappe.throw(_("This scan link has expired. Open a new one from the invoice."))
	if owner != frappe.session.user:
		frappe.throw(
			_("Log in on your phone as {0} to send scans to that invoice.").format(owner),
			frappe.PermissionError,
		)

	result = {"session": session, **parse_scan(scanned_text, manual)}
	frappe.cache.set_value(_result_key(session), result, expires_in_sec=SESSION_TTL)
	frappe.publish_realtime(REALTIME_EVENT, result, user=owner)
	return result


@frappe.whitelist()
def get_scan_result(session):
	"""Polled by the desk form in case the realtime push doesn't arrive."""
	if frappe.cache.get_value(_cache_key(session)) != frappe.session.user:
		return None
	result = frappe.cache.get_value(_result_key(session), use_local_cache=False)
	if result:
		frappe.cache.delete_value(_result_key(session))
	return result


# Text in a PDF attachment that identifies the CU invoice number
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)
_LABELLED_TIMS_RE = re.compile(r"CU\s*(?:INVOICE|INV)[^0-9]{0,40}(\d{17,20})", re.IGNORECASE)
_PDF_MAX_PAGES = 5


def _pdf_text_and_links(content):
	from pypdf import PdfReader

	reader = PdfReader(io.BytesIO(content))
	texts, links = [], []
	for page in reader.pages[:_PDF_MAX_PAGES]:
		texts.append(page.extract_text() or "")
		for annot in page.get("/Annots") or []:
			action = (annot.get_object() or {}).get("/A") or {}
			uri = action.get("/URI")
			if uri:
				links.append(str(uri))
	return "\n".join(texts), links


@frappe.whitelist()
def read_attachment_cuin(doctype, docname, file_url):
	"""Find the CU invoice number in the text and links of a PDF attached to the
	document. QR codes in images, and drawn into PDFs, are decoded in the browser."""
	_check_enabled()
	frappe.has_permission(doctype, "read", docname, throw=True)
	file_name = frappe.db.get_value(
		"File", {"file_url": file_url, "attached_to_doctype": doctype, "attached_to_name": docname}
	)
	if not file_name:
		frappe.throw(_("That file isn't attached to {0}.").format(docname), frappe.PermissionError)

	if not file_url.lower().endswith(".pdf"):
		return None

	try:
		text, links = _pdf_text_and_links(frappe.get_doc("File", file_name).get_content())
	except Exception:
		frappe.log_error(title="CU invoice number: could not read PDF", message=frappe.get_traceback())
		return None

	# Links first (hyperlinked QR targets), then links and numbers printed as text
	for url in links + _URL_RE.findall(text):
		if is_etims_link(url) or (urlparse(url).hostname or "").lower().endswith("kra.go.ke"):
			result = parse_scan(url)
			if result["valid"]:
				return result

	match = _ETIMS_RE.search(text) or _LABELLED_TIMS_RE.search(text)
	if match:
		return parse_scan(match.group(1))
	return None

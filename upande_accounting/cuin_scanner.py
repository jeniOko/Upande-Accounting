"""Scan a KRA ETR / eTIMS receipt QR code into a Control Unit Invoice Number.

Two ways in:
- The desk form scans with the computer's own webcam and calls `parse_scan`.
- The desk form opens a short-lived scan session and shows a QR code; the user
  opens it on their phone (/cuin-scan), scans the receipt there, and the result
  is pushed back to the desk form over realtime for the same user. The result
  is also kept in cache so the desk form can poll for it where realtime is down.

TIMS receipts carry the CU invoice number in the QR link itself. eTIMS receipts
only carry a KRA verification link, so the number is read off that KRA page.
"""

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

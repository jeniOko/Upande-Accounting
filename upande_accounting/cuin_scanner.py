"""Scan a KRA ETR / eTIMS receipt QR code into a Control Unit Invoice Number.

Two ways in:
- The desk form scans with the computer's own webcam and calls `parse_scan`.
- The desk form opens a short-lived scan session and shows a QR code; the user
  opens it on their phone (/cuin-scan), scans the receipt there, and the result
  is pushed back to the desk form over realtime for the same user.
"""

import re
from urllib.parse import parse_qsl, urlparse

import frappe
from frappe import _
from frappe.twofactor import get_qr_svg_code
from frappe.utils import get_url

SESSION_TTL = 15 * 60
REALTIME_EVENT = "cuin_scanned"

# eTIMS: KRACU0100000123/45 ; TIMS: 19-digit CU serial + invoice number
_ETIMS_RE = re.compile(r"\b(KRACU[0-9A-Z]+/\d+)\b", re.IGNORECASE)
_TIMS_RE = re.compile(r"\b(\d{17,20})\b")
_INVOICE_PARAMS = ("invoiceno", "invoice_no", "cuin", "cuinvoiceno", "cu_invoice_no", "invno")


def _cache_key(session):
	return f"upande_accounting:cuin_scan:{session}"


def parse_cuin(text):
	"""Return the CU invoice number found in scanned QR text, or None."""
	text = (text or "").strip()
	if not text:
		return None

	parsed = urlparse(text)
	if parsed.scheme in ("http", "https"):
		for key, value in parse_qsl(parsed.query):
			if key.lower() in _INVOICE_PARAMS and value.strip():
				return value.strip()

	match = _ETIMS_RE.search(text)
	if match:
		return match.group(1).upper()

	match = _TIMS_RE.search(text)
	if match:
		return match.group(1)

	# Not a URL and no known pattern: a plain value is most likely the number itself
	if parsed.scheme not in ("http", "https") and len(text) <= 64 and "\n" not in text:
		return text

	return None


def is_etims_link(text):
	# eTIMS QR codes only carry a verification link (PIN + branch + receipt
	# signature); the CU invoice number is printed on the receipt, not encoded.
	return "etims" in (urlparse((text or "").strip()).netloc or "").lower()


@frappe.whitelist()
def parse_scan(scanned_text):
	return {
		"cuin": parse_cuin(scanned_text),
		"raw": scanned_text,
		"is_etims_link": is_etims_link(scanned_text),
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
def submit_scan(session, scanned_text):
	owner = frappe.cache.get_value(_cache_key(session))
	if not owner:
		frappe.throw(_("This scan link has expired. Open a new one from the invoice."))
	if owner != frappe.session.user:
		frappe.throw(
			_("Log in on your phone as {0} to send scans to that invoice.").format(owner),
			frappe.PermissionError,
		)

	result = parse_scan(scanned_text)
	frappe.publish_realtime(REALTIME_EVENT, {"session": session, **result}, user=owner)
	return result

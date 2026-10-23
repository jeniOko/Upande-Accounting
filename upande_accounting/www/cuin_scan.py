from urllib.parse import quote

import frappe

from upande_accounting.cuin_scanner import is_scanning_enabled

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=" + quote(frappe.request.full_path)
		raise frappe.Redirect

	context.session = frappe.form_dict.get("session") or ""
	context.enabled = is_scanning_enabled()
	context.no_header = 1

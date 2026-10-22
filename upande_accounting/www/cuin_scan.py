from urllib.parse import quote

import frappe

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=" + quote(frappe.request.full_path)
		raise frappe.Redirect

	context.session = frappe.form_dict.get("session") or ""
	context.no_header = 1

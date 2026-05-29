import frappe
from frappe.utils import cint


def sync_tds_from_item_tax_template(doc, method=None):
    items = getattr(doc, "items", [])
    if not items:
        return

    templates = {
        row.item_tax_template
        for row in items
        if row.get("item_tax_template")
    }

    template_tds_map = {}
    if templates:
        results = frappe.get_all(
            "Item Tax Template",
            filters={"name": ["in", list(templates)]},
            fields=["name", "apply_tds"]
        )
        template_tds_map = {r["name"]: cint(r["apply_tds"]) for r in results}

    for row in items:
        template = row.get("item_tax_template")
        row.apply_tds = template_tds_map.get(template, 0) if template else 0
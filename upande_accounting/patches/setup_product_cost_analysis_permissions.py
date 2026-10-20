import frappe
from frappe.permissions import add_permission, update_permission_property

# Cost Analysis fields on Item are permlevel 1: Accounts Manager edits them,
# Item Manager and Stock Manager can only see them.
LEVEL_1_RULES = {
	"Accounts Manager": ("read", "write"),
	"Item Manager": ("read",),
	"Stock Manager": ("read",),
}


def execute():
	for role, ptypes in LEVEL_1_RULES.items():
		if not frappe.db.exists("Role", role):
			continue
		if not frappe.db.exists("Custom DocPerm", {"parent": "Item", "role": role, "permlevel": 1, "if_owner": 0}):
			add_permission("Item", role, permlevel=1)
		for ptype in ptypes:
			update_permission_property("Item", role, 1, ptype, 1, validate=False)

	# Accounts Manager has no standard Item rule, so give it read access to open the form.
	if frappe.db.exists("Role", "Accounts Manager") and not frappe.db.exists(
		"Custom DocPerm", {"parent": "Item", "role": "Accounts Manager", "permlevel": 0}
	):
		add_permission("Item", "Accounts Manager", permlevel=0)

	frappe.clear_cache(doctype="Item")

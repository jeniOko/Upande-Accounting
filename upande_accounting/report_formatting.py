import frappe
from frappe import _
from frappe.locale import get_number_format
from frappe.utils import cint, fmt_money

DEFAULT_DECIMAL_PLACES = 2
MAX_DECIMAL_PLACES = 9


def get_decimal_places(filters):
	value = filters.get("decimal_places")
	if value in (None, ""):
		return DEFAULT_DECIMAL_PLACES
	return min(max(cint(value), 0), MAX_DECIMAL_PLACES)


def format_amount(value, currency, precision):
	"""Like fmt_money, but always shows exactly `precision` decimals (fmt_money trims zeros above 2)."""
	number = fmt_money(value, precision=precision)
	separator = get_number_format().decimal_separator
	if precision and separator:
		whole, _sep, fraction = number.partition(separator)
		number = f"{whole}{separator}{fraction.ljust(precision, '0')}"

	if frappe.defaults.get_global_default("hide_currency_symbol") == "Yes":
		return number
	symbol = frappe.db.get_value("Currency", currency, "symbol", cache=True) or currency
	if frappe.db.get_value("Currency", currency, "symbol_on_right", cache=True):
		return f"{number} {_(symbol)}"
	return f"{_(symbol)} {number}"

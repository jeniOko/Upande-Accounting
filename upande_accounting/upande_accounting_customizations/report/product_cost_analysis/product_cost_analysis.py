# Copyright (c) 2026, jeniffer@upande.com and contributors
# For license information, please see license.txt

"""Product Cost Analysis: cost per unit and margins for each tracked product.

Costs come from expense GL Entries, matched to items through the Cost Sources on the Item.
Production comes from Stock Entries of the item's Production Stock Entry Type, and sales from
Sales Invoices. One aggregated query per source; matching and splitting happen in Python.
"""

from collections import defaultdict
from datetime import date, timedelta

import frappe
from dateutil.relativedelta import relativedelta
from frappe import _
from frappe.utils import cint, flt, fmt_money, get_last_day, getdate

from upande_accounting.product_cost_analysis import COST_SOURCES_FIELD, FIXED_PERCENT, get_cost_source_dimensions

PERIOD_MONTHS = {"Monthly": 1, "Quarterly": 3, "Half-Yearly": 6, "Yearly": 12}
DIRECT, INDIRECT = "Direct", "Indirect"
UNASSIGNED = "Unassigned"
CONSUMING_PURPOSES = ["Manufacture", "Repack"]


def execute(filters=None):
	return ProductCostAnalysis(frappe._dict(filters or {})).run()


@frappe.whitelist()
def get_classifications(txt=None):
	"""Distinct classifications of tracked items, for the MultiSelect filter."""
	values = frappe.get_all(
		"Item",
		filters={"track_cost_analysis": 1, "product_cost_classification": ["like", f"%{txt or ''}%"]},
		pluck="product_cost_classification",
		distinct=True,
	)
	seen = {}
	for value in values:
		seen.setdefault((value or "").strip().lower(), (value or "").strip())
	return [{"value": v, "description": ""} for k, v in sorted(seen.items()) if k]


def as_list(value):
	if not value:
		return []
	if isinstance(value, str):
		value = frappe.parse_json(value) if value.startswith("[") else [value]
	return [v for v in value if v]


def classification_key(value):
	return (value or "").strip().lower()


def safe_div(a, b):
	return a / b if b else None


class ProductCostAnalysis:
	def __init__(self, filters):
		self.filters = filters
		if not filters.company:
			frappe.throw(_("Company is required"))
		self.from_date = getdate(filters.from_date)
		self.to_date = getdate(filters.to_date)
		if self.from_date > self.to_date:
			frappe.throw(_("From Date must be before To Date"))
		self.currency = frappe.get_cached_value("Company", filters.company, "default_currency")
		self.per_unit = cint(filters.show_per_unit)
		self.breakdown = cint(filters.show_cost_breakdown)
		self.group_by_item = filters.group_by == "Item"
		self.warnings = []
		self.set_periods()

	# ------------------------------------------------------------------ setup

	def set_periods(self):
		step = PERIOD_MONTHS.get(self.filters.periodicity or "Monthly", 1)
		self.periods = []
		self.month_to_period = {}
		start = self.from_date
		while start <= self.to_date:
			block_month = ((start.month - 1) // step) * step + 1
			block_end = get_last_day(date(start.year, block_month, 1) + relativedelta(months=step - 1))
			end = min(block_end, self.to_date)
			if step == 1:
				label = start.strftime("%b %Y")
			elif start.year == end.year:
				label = f"{start:%b}–{end:%b %Y}"
			else:
				label = f"{start:%b %Y}–{end:%b %Y}"
			key = f"p{start:%Y%m%d}"
			self.periods.append(frappe._dict(key=key, label=label, from_date=start, to_date=end))
			month = date(start.year, start.month, 1)
			while month <= end:
				self.month_to_period[f"{month:%Y-%m}"] = key
				month += relativedelta(months=1)
			start = end + timedelta(days=1)
		self.period_keys = [p.key for p in self.periods]

	def set_dimensions(self):
		"""Cost Center, Project and accounting dimensions with the values filtered on, if any."""
		self.dimensions = get_cost_source_dimensions()
		self.dimension_by_doctype = {d["document_type"]: d for d in self.dimensions}
		self.dimension_filters = {}
		for d in self.dimensions:
			values = as_list(self.filters.get(d["fieldname"]))
			if values:
				self.dimension_filters[d["fieldname"]] = (d, self.with_descendants(d["document_type"], values))

	def with_descendants(self, doctype, values):
		if not frappe.get_meta(doctype).is_tree:
			return values
		out = set()
		for value in values:
			lft, rgt = frappe.db.get_value(doctype, value, ["lft", "rgt"]) or (None, None)
			if lft is None:
				out.add(value)
				continue
			out.update(frappe.get_all(doctype, filters={"lft": [">=", lft], "rgt": ["<=", rgt]}, pluck="name"))
		return list(out)

	def dimension_conditions(self, alias, doctype, values, prefix):
		conditions = []
		for fieldname, (dimension, filter_values) in self.dimension_filters.items():
			if not frappe.db.has_column(doctype, fieldname):
				self.warnings.append(
					_("{0} has no {1} field, so the {1} filter is not applied to it.").format(
						doctype, dimension["label"]
					)
				)
				continue
			key = f"{prefix}_{fieldname}"
			values[key] = tuple(filter_values)
			conditions.append(f"{alias}.`{fieldname}` in %({key})s")
		return "".join(f" and {c}" for c in conditions)

	def base_values(self):
		return {"company": self.filters.company, "from_date": self.from_date, "to_date": self.to_date}

	def load_items(self):
		rows = frappe.get_all(
			"Item",
			filters={"track_cost_analysis": 1},
			fields=[
				"name",
				"item_name",
				"stock_uom",
				"product_cost_classification",
				"production_entry_type",
				"allocation_basis",
				"allocation_percentage",
				"reporting_uom",
				"reporting_conversion_factor",
			],
			order_by="name",
		)
		sources = defaultdict(list)
		for s in frappe.get_all(
			"Cost Center Related Dimension",
			filters={"parenttype": "Item", "parentfield": COST_SOURCES_FIELD},
			fields=["parent", "accounting_dimension", "dimension_value"],
			order_by="idx",
		):
			sources[s.parent].append((s.accounting_dimension, s.dimension_value))

		self.items = {}
		for r in rows:
			if not r.production_entry_type:
				continue
			self.items[r.name] = frappe._dict(
				code=r.name,
				item_name=r.item_name,
				classification=(r.product_cost_classification or "").strip() or _("Unclassified"),
				key=classification_key(r.product_cost_classification) or "unclassified",
				entry_type=r.production_entry_type,
				basis=r.allocation_basis or "Production Qty",
				pct=flt(r.allocation_percentage),
				uom=r.reporting_uom or r.stock_uom,
				factor=flt(r.reporting_conversion_factor) or 1,
				sources=sources.get(r.name, []),
				prod=defaultdict(float),
				sold=defaultdict(float),
				revenue=defaultdict(float),
				lines={},
				pools={},
				gl_accounts=set(),
			)

		wanted_keys = {classification_key(c) for c in as_list(self.filters.product_cost_classification)}
		self.shown = [
			i
			for i in self.items.values()
			if (not wanted_keys or i.key in wanted_keys) and (not self.filters.item or i.code == self.filters.item)
		]

	def load_cost_centers(self):
		self.cost_centers = {
			c.name: c
			for c in frappe.get_all(
				"Cost Center",
				filters={"company": self.filters.company},
				fields=["name", "parent_cost_center", "cost_type"],
			)
		}
		self._cc_type = {}
		self._ancestors = {}

	def cost_type(self, cost_center):
		"""A cost center's own Cost Type, else its nearest ancestor's, else Direct."""
		if cost_center in self._cc_type:
			return self._cc_type[cost_center]
		cc = self.cost_centers.get(cost_center)
		if not cc:
			value = DIRECT
		elif cc.cost_type:
			value = cc.cost_type
		else:
			value = self.cost_type(cc.parent_cost_center) if cc.parent_cost_center else DIRECT
		self._cc_type[cost_center] = value
		return value

	def ancestors(self, doctype, value):
		"""The value itself and every parent up to the root, nearest first (non-tree doctypes: itself)."""
		key = (doctype, value)
		if key in self._ancestors:
			return self._ancestors[key]
		chain = []
		if value:
			chain.append(value)
			meta = frappe.get_meta(doctype)
			if meta.is_tree:
				parent_field = meta.nsm_parent_field or f"parent_{frappe.scrub(doctype)}"
				if doctype == "Cost Center":
					parent = self.cost_centers.get(value, {}).get("parent_cost_center")
				else:
					parent = frappe.db.get_value(doctype, value, parent_field)
				if parent and parent != value:
					chain.extend(self.ancestors(doctype, parent))
		self._ancestors[key] = chain
		return chain

	# ------------------------------------------------------------------ queries

	def load_production(self):
		if not self.items:
			return
		values = self.base_values()
		values["items"] = tuple(self.items)
		values["entry_types"] = tuple({i.entry_type for i in self.items.values()})
		dims = self.dimension_conditions("sed", "Stock Entry Detail", values, "prod")
		for r in frappe.db.sql(
			f"""
			select sed.item_code, se.stock_entry_type, date_format(se.posting_date, '%%Y-%%m') as month,
				sum(sed.transfer_qty) as qty
			from `tabStock Entry Detail` sed
			inner join `tabStock Entry` se on se.name = sed.parent
			where se.docstatus = 1 and se.company = %(company)s
				and se.posting_date between %(from_date)s and %(to_date)s
				and se.stock_entry_type in %(entry_types)s
				and sed.item_code in %(items)s
				and ifnull(sed.t_warehouse, '') != ''
				and ifnull(sed.is_scrap_item, 0) = 0
				{dims}
			group by sed.item_code, se.stock_entry_type, month
			""",
			values,
			as_dict=True,
		):
			item = self.items[r.item_code]
			if r.stock_entry_type == item.entry_type:
				item.prod[self.month_to_period[r.month]] += flt(r.qty) * item.factor

	def load_sales(self):
		if not self.items:
			return
		values = self.base_values()
		values["items"] = tuple(self.items)
		dims = self.dimension_conditions("sii", "Sales Invoice Item", values, "sales")
		for r in frappe.db.sql(
			f"""
			select sii.item_code, date_format(si.posting_date, '%%Y-%%m') as month,
				sum(sii.stock_qty) as qty, sum(sii.base_net_amount) as amount
			from `tabSales Invoice Item` sii
			inner join `tabSales Invoice` si on si.name = sii.parent
			where si.docstatus = 1 and si.company = %(company)s
				and si.posting_date between %(from_date)s and %(to_date)s
				and sii.item_code in %(items)s
				{dims}
			group by sii.item_code, month
			""",
			values,
			as_dict=True,
		):
			item = self.items[r.item_code]
			period = self.month_to_period[r.month]
			item.sold[period] += flt(r.qty) * item.factor
			item.revenue[period] += flt(r.amount)

	def load_gl(self):
		values = self.base_values()
		columns = ["cost_center", "project"] + [
			d["fieldname"]
			for d in self.dimensions
			if d["fieldname"] not in ("cost_center", "project") and frappe.db.has_column("GL Entry", d["fieldname"])
		]
		self.gl_dimension_columns = columns
		select = ", ".join(f"gle.`{c}`" for c in columns)
		dims = self.dimension_conditions("gle", "GL Entry", values, "gl")
		self.gl_rows = frappe.db.sql(
			f"""
			select gle.account, {select}, date_format(gle.posting_date, '%%Y-%%m') as month,
				sum(gle.debit - gle.credit) as amount
			from `tabGL Entry` gle
			inner join `tabAccount` acc on acc.name = gle.account
			where gle.company = %(company)s and gle.is_cancelled = 0
				and gle.posting_date between %(from_date)s and %(to_date)s
				and acc.root_type = 'Expense'
				and gle.voucher_type != 'Period Closing Voucher'
				{dims}
			group by gle.account, {select}, month
			having amount != 0
			""",
			values,
			as_dict=True,
		)
		accounts = {r.account for r in self.gl_rows}
		parents = {}
		if accounts:
			parents = dict(
				frappe.get_all(
					"Account", filters={"name": ["in", list(accounts)]}, fields=["name", "parent_account"], as_list=True
				)
			)
		names = dict(
			frappe.get_all(
				"Account",
				filters={"name": ["in", list(set(parents.values()) or [""])]},
				fields=["name", "account_name"],
				as_list=True,
			)
		)
		self.account_category = {a: names.get(parents.get(a)) or a for a in accounts}

	def load_consumption(self):
		"""Tracked items consumed by entries that produce other tracked items (e.g. milk into yoghurt).

		Returns {output item: {input item: {period: input stock qty}}}. Consumed qty is shared across
		an entry's tracked outputs by output qty."""
		self.chain = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
		# Only entry types that consume materials; harvest-style receipts can run to millions of rows.
		entry_types = frappe.get_all(
			"Stock Entry Type",
			filters={
				"name": ["in", list({i.entry_type for i in self.items.values()}) or [""]],
				"purpose": ["in", CONSUMING_PURPOSES],
			},
			pluck="name",
		)
		if not entry_types:
			return
		values = self.base_values()
		values["items"] = tuple(self.items)
		values["entry_types"] = tuple(entry_types)
		dims = self.dimension_conditions("sed", "Stock Entry Detail", values, "chain")
		rows = frappe.db.sql(
			f"""
			select sed.parent, sed.item_code, se.stock_entry_type,
				date_format(se.posting_date, '%%Y-%%m') as month,
				if(ifnull(sed.t_warehouse, '') != '', 'out', 'in') as direction,
				sum(sed.transfer_qty) as qty
			from `tabStock Entry Detail` sed
			inner join `tabStock Entry` se on se.name = sed.parent
			where se.docstatus = 1 and se.company = %(company)s
				and se.posting_date between %(from_date)s and %(to_date)s
				and se.stock_entry_type in %(entry_types)s
				and sed.item_code in %(items)s
				and ifnull(sed.is_scrap_item, 0) = 0
				and (ifnull(sed.t_warehouse, '') = '' or (1 = 1 {dims}))
				and exists (
					select 1 from `tabStock Entry Detail` c
					where c.parent = se.name and c.item_code in %(items)s
						and ifnull(c.s_warehouse, '') != '' and ifnull(c.t_warehouse, '') = ''
				)
			group by sed.parent, sed.item_code, se.stock_entry_type, month, direction
			""",
			values,
			as_dict=True,
		)
		entries = defaultdict(lambda: {"out": defaultdict(float), "in": defaultdict(float), "month": None})
		for r in rows:
			entry = entries[r.parent]
			entry["month"] = r.month
			if r.direction == "out" and r.stock_entry_type != self.items[r.item_code].entry_type:
				continue
			entry[r.direction][r.item_code] += flt(r.qty)

		for entry in entries.values():
			outputs = entry["out"]
			total_out = sum(outputs.values())
			if not total_out:
				continue
			period = self.month_to_period[entry["month"]]
			for input_code, qty in entry["in"].items():
				for output_code, out_qty in outputs.items():
					if output_code != input_code:
						self.chain[output_code][input_code][period] += qty * out_qty / total_out

	# ------------------------------------------------------------------ matching and splitting

	def build_source_index(self):
		self.cc_sources = defaultdict(list)
		self.dim_sources = defaultdict(list)
		for item in self.items.values():
			for doctype, value in item.sources:
				if doctype == "Cost Center":
					self.cc_sources[value].append(item.code)
				else:
					self.dim_sources[(doctype, value)].append(item.code)

	def match(self, row):
		"""{item code: matching source (doctype, value)}. Cost center sources are tried first and the
		nearest one wins, so a row counts once per item."""
		matched = {}
		for cc in self.ancestors("Cost Center", row.cost_center):
			for code in self.cc_sources.get(cc, ()):
				matched.setdefault(code, ("Cost Center", cc))
		for d in self.dimensions:
			if d["document_type"] == "Cost Center" or d["fieldname"] not in self.gl_dimension_columns:
				continue
			for value in self.ancestors(d["document_type"], row.get(d["fieldname"])):
				for code in self.dim_sources.get((d["document_type"], value), ()):
					matched.setdefault(code, (d["document_type"], value))
		return matched

	def basis_value(self, code, basis, period):
		item = self.items[code]
		series = item.revenue if basis == "Revenue" else item.prod
		return series[period] if period else sum(series.values())

	def split(self, codes, period, cost_center):
		"""{item code or None (unallocated): (share, basis label)} for a row matched by several items."""
		cache_key = (codes, period)
		if cache_key in self._split_cache:
			return self._split_cache[cache_key]

		if len(codes) == 1:
			result = {codes[0]: (1.0, _("sole user"))}
		else:
			fixed = [c for c in codes if self.items[c].basis == FIXED_PERCENT]
			total_fixed = sum(self.items[c].pct for c in fixed)
			if total_fixed > 100.0001:
				frappe.throw(
					_("Fixed allocation percentages on cost center {0} add up to {1}%: {2}").format(
						frappe.bold(cost_center or _("(none)")),
						flt(total_fixed, 2),
						", ".join(f"{c} ({flt(self.items[c].pct, 2)}%)" for c in fixed),
					),
					title=_("Fixed % Over 100"),
				)
			result = {c: (self.items[c].pct / 100, _("fixed {0}%").format(flt(self.items[c].pct, 2))) for c in fixed}
			others = [c for c in codes if c not in result]
			remainder = 1 - total_fixed / 100
			if remainder > 1e-9:
				if others:
					result.update(self.share_by_basis(others, period, remainder))
				else:
					result[None] = (remainder, _("unallocated remainder"))

		self._split_cache[cache_key] = result
		return result

	def share_by_basis(self, codes, period, remainder):
		raw = {}
		labels = {}
		for code in codes:
			basis = self.items[code].basis
			label = _("revenue share") if basis == "Revenue" else _("production qty share")
			values = {c: self.basis_value(c, basis, period) for c in codes}
			if not sum(values.values()):
				values = {c: self.basis_value(c, basis, None) for c in codes}
				label = _("{0}, full range").format(label)
			total = sum(values.values())
			if total:
				raw[code] = values[code] / total
			else:
				raw[code] = 1 / len(codes)
				label = _("equal split, no production or revenue")
			labels[code] = label
		total_raw = sum(raw.values()) or 1
		return {c: (remainder * raw[c] / total_raw, labels[c]) for c in codes}

	def add_line(self, item, kind, source, category, period, amount, account=None, label=None):
		key = (kind, source, category)
		line = item.lines.get(key)
		if not line:
			line = item.lines[key] = frappe._dict(
				kind=kind, source=source, category=category, label=label or category,
				amounts=defaultdict(float), accounts=set(),
			)
		line.amounts[period] += amount
		if account:
			line.accounts.add(account)
			item.gl_accounts.add(account)

	def allocate_costs(self):
		self._split_cache = {}
		self.unassigned = defaultdict(lambda: frappe._dict(amounts=defaultdict(float), accounts=set()))
		for row in self.gl_rows:
			period = self.month_to_period[row.month]
			amount = flt(row.amount)
			matched = self.match(row)
			if not matched:
				bucket = self.unassigned[row.cost_center or ""]
				bucket.amounts[period] += amount
				bucket.accounts.add(row.account)
				continue

			codes = tuple(sorted(matched))
			for code, (share, basis) in self.split(codes, period, row.cost_center).items():
				if code is None:
					bucket = self.unassigned[row.cost_center or ""]
					bucket.amounts[period] += amount * share
					bucket.accounts.add(row.account)
					continue
				item = self.items[code]
				source = matched[code]
				kind = self.cost_type(row.cost_center) if source[0] == "Cost Center" else DIRECT
				self.add_line(item, kind, source, self.account_category[row.account], period, amount * share, row.account)

				pool = item.pools.setdefault(
					source, frappe._dict(kind=kind, total=0.0, allocated=0.0, bases=set())
				)
				pool.total += amount
				pool.allocated += amount * share
				pool.bases.add(basis)

	def chain_order(self):
		"""Tracked items with their chained inputs first. A cycle stops the report."""
		order, state = [], {}

		def visit(code, path):
			if state.get(code) == "done":
				return
			if state.get(code) == "visiting":
				cycle = path[path.index(code) :] + [code]
				frappe.throw(
					_("Chained inputs form a loop: {0}").format(" → ".join(frappe.bold(c) for c in cycle)),
					title=_("Circular Input Chain"),
				)
			state[code] = "visiting"
			for input_code in self.chain.get(code, {}):
				visit(input_code, path + [code])
			state[code] = "done"
			order.append(code)

		for code in self.items:
			visit(code, [])
		return order

	def add_chained_inputs(self):
		for code in self.chain_order():
			item = self.items[code]
			for input_code, qty_by_period in self.chain.get(code, {}).items():
				source = self.items[input_code]
				range_cpu = safe_div(self.total_cost(source), sum(source.prod.values()))
				category = _("Input: {0}").format(source.classification)
				for period, qty in qty_by_period.items():
					cpu = safe_div(self.total_cost(source, period), source.prod[period])
					if cpu is None:
						cpu = range_cpu
					if cpu is None:
						self.warnings.append(
							_("{0} consumes {1}, which has no production in the range, so the input is not costed.").format(
								frappe.bold(code), frappe.bold(input_code)
							)
						)
						continue
					self.add_line(item, DIRECT, ("Item", input_code), category, period, qty * source.factor * cpu)

	def total_cost(self, item, period=None):
		if period:
			return sum(line.amounts[period] for line in item.lines.values())
		return sum(sum(line.amounts.values()) for line in item.lines.values())

	# ------------------------------------------------------------------ figures

	def sold_portion(self, item, amounts):
		"""Cost of units sold per period for a series of production costs; None when the item sold
		units but produced nothing in the whole range."""
		range_prod = sum(item.prod.values())
		range_amount = sum(amounts.values())
		out = {}
		for p in self.period_keys:
			sold = item.sold[p]
			if not sold:
				out[p] = 0.0
			elif item.prod[p]:
				out[p] = amounts[p] * sold / item.prod[p]
			elif range_prod:
				out[p] = range_amount * sold / range_prod
			else:
				return None
		return out

	def item_figures(self, item):
		cost = {DIRECT: defaultdict(float), INDIRECT: defaultdict(float)}
		for line in item.lines.values():
			for p, amount in line.amounts.items():
				cost[line.kind][p] += amount
		flags = []
		gap_periods = [p.label for p in self.periods if item.sold[p.key] and not item.prod[p.key]]
		no_production = sum(item.sold.values()) and not sum(item.prod.values())
		if no_production:
			flags.append(_("Sold but not produced in range: cost of units sold not calculated"))
		elif gap_periods:
			flags.append(_("No production in {0}: range cost per unit used").format(", ".join(gap_periods)))
		return frappe._dict(
			prod=item.prod,
			sold=item.sold,
			revenue=item.revenue,
			direct=cost[DIRECT],
			indirect=cost[INDIRECT],
			direct_sold=self.sold_portion(item, cost[DIRECT]),
			indirect_sold=self.sold_portion(item, cost[INDIRECT]),
			flags=flags,
		)

	@staticmethod
	def sum_figures(figures):
		def add(series):
			out = defaultdict(float)
			for s in series:
				for k, v in (s or {}).items():
					out[k] += v
			return out

		return frappe._dict(
			prod=add(f.prod for f in figures),
			sold=add(f.sold for f in figures),
			revenue=add(f.revenue for f in figures),
			direct=add(f.direct for f in figures),
			indirect=add(f.indirect for f in figures),
			direct_sold=add(f.direct_sold for f in figures),
			indirect_sold=add(f.indirect_sold for f in figures),
			flags=[_("Includes items with incomplete figures")] if any(f.flags for f in figures) else [],
		)

	def figures_row(self, f):
		produced = sum(f.prod.values())
		sold = sum(f.sold.values())
		revenue = sum(f.revenue.values())
		production_cost = sum(f.direct.values()) + sum(f.indirect.values())
		direct_sold = sum(f.direct_sold.values()) if f.direct_sold is not None else None
		indirect_sold = sum(f.indirect_sold.values()) if f.indirect_sold is not None else None
		cm = revenue - direct_sold if direct_sold is not None else None
		nm = cm - indirect_sold if cm is not None and indirect_sold is not None else None

		row = {
			"produced_qty": produced,
			"sold_qty": sold,
			"revenue": revenue,
			"avg_price": safe_div(revenue, sold),
			"direct_cost": self.unit(direct_sold, sold),
			"contribution_margin": self.unit(cm, sold),
			"cm_pct": safe_div(cm * 100, revenue) if cm is not None else None,
			"indirect_cost": self.unit(indirect_sold, sold),
			"net_margin": self.unit(nm, sold),
			"nm_pct": safe_div(nm * 100, revenue) if nm is not None else None,
			"production_cost": production_cost,
			"cost_per_unit": safe_div(production_cost, produced),
			"flags": "; ".join(f.flags),
		}
		for p in self.period_keys:
			cost_p = f.direct[p] + f.indirect[p]
			row[f"{p}_produced_qty"] = f.prod[p]
			row[f"{p}_revenue"] = f.revenue[p]
			row[f"{p}_cost_per_unit"] = safe_div(cost_p, f.prod[p])
			if f.direct_sold is not None and f.indirect_sold is not None:
				nm_p = f.revenue[p] - f.direct_sold[p] - f.indirect_sold[p]
				row[f"{p}_net_margin"] = self.unit(nm_p, f.sold[p])
			row[f"_avg_price_{p}"] = safe_div(f.revenue[p], f.sold[p])
		return row

	def unit(self, amount, qty):
		if amount is None:
			return None
		return safe_div(amount, qty) if self.per_unit else amount

	# ------------------------------------------------------------------ rows

	def run(self):
		self.set_dimensions()
		self.load_items()
		self.load_cost_centers()
		self.build_source_index()
		self.load_production()
		self.load_sales()
		self.load_gl()
		self.load_consumption()
		self.allocate_costs()
		self.add_chained_inputs()
		self.check_data_quality()

		data, top_rows = self.build_rows()
		return self.get_columns(), data, self.get_message(), self.get_chart(top_rows), self.get_summary(top_rows)

	def build_rows(self):
		data, top_rows = [], []
		groups = defaultdict(list)
		for item in self.shown:
			groups[item.key].append(item)

		if self.group_by_item:
			for item in sorted(self.shown, key=lambda i: i.code):
				row = self.item_row(item, indent=0)
				top_rows.append(row)
				data.append(row)
				data.extend(self.breakdown_rows(item, indent=1))
		else:
			for key in sorted(groups, key=lambda k: groups[k][0].classification.lower()):
				items = groups[key]
				figures = self.sum_figures([self.item_figures(i) for i in items])
				row = self.figures_row(figures)
				row.update(
					product=items[0].classification,
					uom=items[0].uom,
					indent=0,
					_drill=self.drill(items),
				)
				top_rows.append(row)
				data.append(row)
				for item in items:
					data.append(self.item_row(item, indent=1))
					data.extend(self.breakdown_rows(item, indent=2))

		data.extend(self.unassigned_rows())
		for row in data:
			row["currency"] = self.currency
		return data, top_rows

	def item_row(self, item, indent):
		row = self.figures_row(self.item_figures(item))
		label = item.code if item.item_name in (None, "", item.code) else f"{item.code}: {item.item_name}"
		row.update(product=label, item_code=item.code, uom=item.uom, indent=indent, _drill=self.drill([item]))
		return row

	def breakdown_rows(self, item, indent):
		if not self.breakdown or not item.lines:
			return []
		produced = sum(item.prod.values())
		rows = []

		def line_row(label, amounts, kind, level, accounts=(), sources=None, allocation=None):
			sold = self.sold_portion(item, amounts)
			sold_total = sum(sold.values()) if sold is not None else None
			row = {
				"product": label,
				"indent": level,
				"production_cost": sum(amounts.values()),
				"cost_per_unit": safe_div(sum(amounts.values()), produced),
				"direct_cost" if kind == DIRECT else "indirect_cost": self.unit(sold_total, sum(item.sold.values())),
				"allocation": allocation,
				"_breakdown": 1,
			}
			for p in self.period_keys:
				row[f"{p}_cost_per_unit"] = safe_div(amounts[p], item.prod[p])
			if accounts:
				row["_drill"] = {"gl": self.gl_filters(accounts, sources or item.sources)}
			return row

		def merged(lines):
			out = defaultdict(float)
			for line in lines:
				for p, v in line.amounts.items():
					out[p] += v
			return out

		for kind in (DIRECT, INDIRECT):
			kind_lines = [l for l in item.lines.values() if l.kind == kind]
			if not kind_lines:
				continue
			all_accounts = set().union(*(l.accounts for l in kind_lines))
			rows.append(line_row(_(kind), merged(kind_lines), kind, indent, all_accounts))

			if kind == DIRECT:
				by_category = defaultdict(list)
				for line in kind_lines:
					by_category[line.category].append(line)
				for category in sorted(by_category):
					lines = by_category[category]
					accounts = set().union(*(l.accounts for l in lines))
					rows.append(line_row(category, merged(lines), kind, indent + 1, accounts))
				continue

			by_source = defaultdict(list)
			for line in kind_lines:
				by_source[line.source].append(line)
			for source in sorted(by_source, key=lambda s: s[1]):
				lines = by_source[source]
				accounts = set().union(*(l.accounts for l in lines))
				rows.append(
					line_row(
						source[1], merged(lines), kind, indent + 1, accounts, [source], self.trail(item, source)
					)
				)
				by_category = defaultdict(list)
				for line in lines:
					by_category[line.category].append(line)
				for category in sorted(by_category):
					cat_lines = by_category[category]
					cat_accounts = set().union(*(l.accounts for l in cat_lines))
					rows.append(line_row(category, merged(cat_lines), kind, indent + 2, cat_accounts, [source]))
		return rows

	def trail(self, item, source):
		pool = item.pools.get(source)
		if not pool or not pool.total:
			return None
		share = pool.allocated / pool.total * 100
		return "{0} {1} × {2}% ({3}) = {4}".format(
			source[1],
			fmt_money(pool.total, precision=0),
			flt(share, 1),
			", ".join(sorted(pool.bases)),
			fmt_money(pool.allocated, precision=0),
		)

	def unassigned_rows(self):
		if not self.unassigned:
			return []
		total = sum(sum(b.amounts.values()) for b in self.unassigned.values())
		rows = [
			{
				"product": _(UNASSIGNED),
				"indent": 0,
				"production_cost": total,
				"flags": _("Expense GL that matched no tracked item"),
				"_unassigned": 1,
			}
		]
		for cc in sorted(self.unassigned, key=lambda c: -abs(sum(self.unassigned[c].amounts.values()))):
			bucket = self.unassigned[cc]
			rows.append(
				{
					"product": cc or _("(No Cost Center)"),
					"indent": 1,
					"production_cost": sum(bucket.amounts.values()),
					"_unassigned": 1,
					"_drill": {
						"gl": self.gl_filters(bucket.accounts, [("Cost Center", cc)] if cc else [])
					},
				}
			)
		return rows

	# ------------------------------------------------------------------ drill-down

	def gl_filters(self, accounts, sources):
		filters = {
			"company": self.filters.company,
			"from_date": str(self.from_date),
			"to_date": str(self.to_date),
			"account": sorted(accounts),
			"categorize_by": "Categorize by Voucher (Consolidated)",
		}
		for fieldname, (dimension, values) in self.dimension_filters.items():
			filters[fieldname] = as_list(self.filters.get(fieldname))
		# The report's own dimension filters are kept as they are; sources only narrow unfiltered ones.
		cost_centers = [v for d, v in sources if d == "Cost Center"]
		if cost_centers:
			filters.setdefault("cost_center", sorted(set(cost_centers)))
		else:
			source_values = defaultdict(set)
			for doctype, value in sources:
				dimension = self.dimension_by_doctype.get(doctype)
				if dimension:
					source_values[dimension["fieldname"]].add(value)
			for fieldname, values in source_values.items():
				filters.setdefault(fieldname, sorted(values))
		return filters

	def drill(self, items):
		accounts = set().union(*(i.gl_accounts for i in items)) if items else set()
		sources = [s for i in items for s in i.sources]
		drill = {"gl": self.gl_filters(accounts, sources)} if accounts else {}
		if len(items) == 1:
			base = {"company": self.filters.company, "from_date": str(self.from_date), "to_date": str(self.to_date)}
			drill["stock"] = {**base, "item_code": items[0].code}
			drill["sales"] = {**base, "item_code": items[0].code}
		return drill

	# ------------------------------------------------------------------ data quality

	def check_data_quality(self):
		groups = defaultdict(set)
		for item in self.shown:
			groups[item.classification].add(item.uom)
		for classification, uoms in groups.items():
			if len(uoms) > 1 and not self.group_by_item:
				self.warnings.append(
					_("{0} mixes reporting UOMs ({1}), so its quantity totals are not comparable.").format(
						frappe.bold(classification), ", ".join(sorted(uoms))
					)
				)

		if self.dimension_filters and self.items:
			self.check_missing_dimensions()

	def check_missing_dimensions(self):
		values = self.base_values()
		values["items"] = tuple(self.items)
		values["entry_types"] = tuple({i.entry_type for i in self.items.values()})
		for fieldname, (dimension, _values) in self.dimension_filters.items():
			counts = {}
			if frappe.db.has_column("Stock Entry Detail", fieldname):
				counts[_("production rows")] = frappe.db.sql(
					f"""
					select count(*) from `tabStock Entry Detail` sed
					inner join `tabStock Entry` se on se.name = sed.parent
					where se.docstatus = 1 and se.company = %(company)s
						and se.posting_date between %(from_date)s and %(to_date)s
						and se.stock_entry_type in %(entry_types)s and sed.item_code in %(items)s
						and ifnull(sed.t_warehouse, '') != '' and ifnull(sed.`{fieldname}`, '') = ''
					""",
					values,
				)[0][0]
			if frappe.db.has_column("Sales Invoice Item", fieldname):
				counts[_("sales invoice rows")] = frappe.db.sql(
					f"""
					select count(*) from `tabSales Invoice Item` sii
					inner join `tabSales Invoice` si on si.name = sii.parent
					where si.docstatus = 1 and si.company = %(company)s
						and si.posting_date between %(from_date)s and %(to_date)s
						and sii.item_code in %(items)s and ifnull(sii.`{fieldname}`, '') = ''
					""",
					values,
				)[0][0]
			missing = ", ".join(f"{n} {label}" for label, n in counts.items() if n)
			if missing:
				self.warnings.append(
					_("{0} for tracked items have no {1}, so they are left out by the {1} filter.").format(
						missing, dimension["label"]
					)
				)

	# ------------------------------------------------------------------ output

	def get_columns(self):
		first = _("Item") if self.group_by_item else _("Product / Item")
		cost_label = _(" per Unit Sold") if self.per_unit else ""
		columns = [
			{"fieldname": "product", "label": first, "fieldtype": "Data", "width": 280},
			{"fieldname": "uom", "label": _("Reporting UOM"), "fieldtype": "Link", "options": "UOM", "width": 90},
			{"fieldname": "produced_qty", "label": _("Produced Qty"), "fieldtype": "Float", "width": 120},
			{"fieldname": "sold_qty", "label": _("Sold Qty"), "fieldtype": "Float", "width": 110},
			{"fieldname": "revenue", "label": _("Revenue"), "fieldtype": "Currency", "options": "currency", "width": 130},
			{"fieldname": "avg_price", "label": _("Avg Selling Price"), "fieldtype": "Currency", "options": "currency", "width": 120},
			{"fieldname": "direct_cost", "label": _("Direct Cost") + cost_label, "fieldtype": "Currency", "options": "currency", "width": 130},
			{"fieldname": "contribution_margin", "label": _("Contribution Margin") + cost_label, "fieldtype": "Currency", "options": "currency", "width": 140},
			{"fieldname": "cm_pct", "label": _("CM %"), "fieldtype": "Percent", "width": 80},
			{"fieldname": "indirect_cost", "label": _("Indirect Cost") + cost_label, "fieldtype": "Currency", "options": "currency", "width": 130},
			{"fieldname": "net_margin", "label": _("Net Margin") + cost_label, "fieldtype": "Currency", "options": "currency", "width": 130},
			{"fieldname": "nm_pct", "label": _("NM %"), "fieldtype": "Percent", "width": 80},
			{"fieldname": "production_cost", "label": _("Production Cost"), "fieldtype": "Currency", "options": "currency", "width": 140},
			{"fieldname": "cost_per_unit", "label": _("Cost per Unit"), "fieldtype": "Currency", "options": "currency", "width": 120},
		]
		if self.breakdown:
			columns.append({"fieldname": "allocation", "label": _("Allocation"), "fieldtype": "Data", "width": 320})
		columns.append({"fieldname": "flags", "label": _("Flags"), "fieldtype": "Data", "width": 260})

		if len(self.periods) > 1:
			for p in self.periods:
				columns += [
					{"fieldname": f"{p.key}_produced_qty", "label": _("{0} Produced").format(p.label), "fieldtype": "Float", "width": 120},
					{"fieldname": f"{p.key}_revenue", "label": _("{0} Revenue").format(p.label), "fieldtype": "Currency", "options": "currency", "width": 130},
					{"fieldname": f"{p.key}_cost_per_unit", "label": _("{0} Cost/Unit").format(p.label), "fieldtype": "Currency", "options": "currency", "width": 120},
					{"fieldname": f"{p.key}_net_margin", "label": _("{0} Net Margin").format(p.label) + cost_label, "fieldtype": "Currency", "options": "currency", "width": 130},
				]
		return columns

	def get_message(self):
		notes = []
		if not self.items:
			notes.append(
				_("No items have Track Cost Analysis ticked. The Unassigned row shows all expense GL for the period.")
			)
		notes.extend(dict.fromkeys(self.warnings))
		if not notes:
			return None
		items = "".join(f"<li>{n}</li>" for n in notes)
		return f"<div class='text-warning'><ul style='margin-bottom: 0'>{items}</ul></div>"

	def get_chart(self, top_rows):
		rows = [r for r in top_rows if r.get("produced_qty") or r.get("sold_qty")][:4]
		if not rows or not self.periods:
			return None
		datasets = []
		for row in rows:
			datasets.append(
				{"name": _("{0} Cost/Unit").format(row["product"]), "values": [flt(row.get(f"{p}_cost_per_unit")) for p in self.period_keys]}
			)
			datasets.append(
				{"name": _("{0} Avg Price").format(row["product"]), "values": [flt(row.get(f"_avg_price_{p}")) for p in self.period_keys]}
			)
		return {
			"data": {"labels": [p.label for p in self.periods], "datasets": datasets},
			"type": "line",
			"fieldtype": "Currency",
			"options": "currency",
			"currency": self.currency,
		}

	def get_summary(self, top_rows):
		revenue = sum(flt(r.get("revenue")) for r in top_rows)
		production_cost = sum(flt(r.get("production_cost")) for r in top_rows)
		unassigned = sum(sum(b.amounts.values()) for b in self.unassigned.values())
		summary = [
			{"value": revenue, "label": _("Revenue"), "datatype": "Currency", "currency": self.currency},
			{"value": production_cost, "label": _("Production Cost"), "datatype": "Currency", "currency": self.currency},
		]
		if not self.per_unit:
			net = [r.get("net_margin") for r in top_rows]
			if net and all(v is not None for v in net):
				total = sum(net)
				summary.append(
					{
						"value": total,
						"label": _("Net Margin"),
						"datatype": "Currency",
						"currency": self.currency,
						"indicator": "Green" if total >= 0 else "Red",
					}
				)
		summary.append(
			{
				"value": unassigned,
				"label": _("Unassigned Cost"),
				"datatype": "Currency",
				"currency": self.currency,
				"indicator": "Orange" if unassigned else "Green",
			}
		)
		return summary

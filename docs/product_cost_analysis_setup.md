# Product Cost Analysis: setup guide

The **Product Cost Analysis** report shows, for each product you sell, what it cost to produce, the cost per unit and the margin earned. It reads your existing GL entries, stock entries and sales invoices and changes nothing.

Setup takes three steps.

## 1. Check how clean your costs are

Before you configure anything, open **Product Cost Analysis** and run it for last month. No products are set up yet, so every expense shows in the **Unassigned** row, grouped by cost center.

This shows you which cost centers carry your costs. Click any amount to open the General Ledger behind it.

## 2. Mark each cost center Direct or Indirect

Open each cost center that carries production costs and set **Cost Type**:

- **Direct**: costs that belong to one product or product line, such as a greenhouse, the dairy herd or the poultry unit.
- **Indirect**: shared overheads such as the packhouse, fleet and workshop, administration or IT.

If you leave Cost Type blank, the cost center takes the value of its parent. A top-level cost center with no value counts as Direct, so you can set a group once and its children follow.

## 3. Set up each product on the Item

Open the item. If **Allow Sales** is ticked, it has a **Cost Analysis** tab. Tick **Track Cost Analysis** and fill in:

| Field | What to enter |
|---|---|
| Product Cost Classification | The product line this item rolls up into, e.g. *Roses*, *Milk*, *Yoghurt*. Items with the same label are grouped. Upper and lower case are treated the same. |
| Production Stock Entry Type | The stock entry type you record production with, e.g. *Harvesting*, *Milking* or *Manufacture*. |
| Cost Sources | The cost centers (or projects or other accounting dimensions) whose expenses belong to this item. A cost center includes all its child cost centers. List both the direct cost centers and the shared overheads the product uses. |
| Indirect Cost Allocation Basis | How the item's share of costs it shares with other items is worked out: **Production Qty** (default), **Revenue**, or **Fixed %**. |
| Allocation % | Required only for Fixed %. The fixed shares of items that share a cost center may not add up to more than 100%. |
| Reporting UOM and Conversion Factor | The unit for cost per unit, e.g. *Kg* or *Stem*. Reporting qty = stock qty × conversion factor. Leave the factor at 1 if the reporting UOM is the stock UOM. |

Only Accounts Managers can edit these fields. Item Managers and Stock Managers can see them.

**Example, cut roses:** every rose variety uses Classification *Roses*, Production Stock Entry Type *Harvesting*, and lists the greenhouse cost centers (Direct) plus Packhouse and HR & Admin (Indirect). Greenhouse costs are not tracked by variety, so they are shared across varieties by stems harvested.

**Example, yoghurt made from your own milk:** set up milk as its own product. When yoghurt is produced through a Manufacture entry that consumes milk, the report adds the milk at milk's cost per unit as a direct cost line, *Input: Milk*.

## Reading the report

| Column | Meaning |
|---|---|
| Produced Qty / Sold Qty | Production and sales in the reporting UOM. |
| Revenue, Avg Selling Price | Net invoiced amount. Credit notes reduce it. |
| Direct Cost, Indirect Cost | Cost of the units **sold** = cost per unit × sold qty. |
| Contribution Margin | Revenue − direct cost: does the product pay for itself? |
| Net Margin | Contribution margin − indirect cost: does it also cover its share of overheads? |
| Production Cost | All costs charged to the product in the period. |
| Cost per Unit | Production cost ÷ produced qty. |

- **Show Cost Breakdown** splits each item into Direct and Indirect costs by account group. Indirect lines show the allocation, e.g. *Packhouse 3,400,000 × 35% (production qty share) = 1,190,000*.
- **Show Per Unit** shows costs and margins per unit sold.
- **Periodicity** adds produced qty, revenue, cost per unit and net margin for each period.
- **Cost Center, Project and dimension filters** (e.g. Farm) narrow costs, production and sales together. If production or sales rows are missing the dimension, the report warns you.
- Click a cost figure to open the General Ledger, Produced Qty to open the Stock Ledger, or Sold Qty and Revenue to open the Item-wise Sales Register.

### Flags and warnings

- **No production in a period:** the item sold units but produced none that period, so the report uses its cost per unit for the whole date range.
- **Sold but not produced in range:** cost of units sold cannot be calculated. Widen the date range.
- **Mixed reporting UOMs:** items in one classification use different units, so its quantity totals are not comparable.

## Tips

- Costs and production must fall in the same period to give a fair cost per unit. Monthly suits milk and eggs. Roses read better quarterly, and tree crops need a season-length range.
- Do not list cost centers of crops that are not yet producing as cost sources. Their costs would inflate the cost per unit of the crops you are harvesting.
- Expenses posted to a cost center that no product lists stay in **Unassigned**. Check that row regularly.

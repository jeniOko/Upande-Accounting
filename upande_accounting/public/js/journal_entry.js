frappe.ui.form.on("Journal Entry", {
	refresh: function (frm) {
		// Core (erpnext.journal_entry.lock_reversal_entry, triggered on
		// refresh whenever reversal_of is set on a draft) locks every field
		// on a reversal entry, including Posting Date, so the reversal is
		// forced to carry the same date as the original entry. Re-open just
		// the date so a reversal can be posted into a different period.
		if (frm.doc.reversal_of && (frm.is_new() || frm.doc.docstatus === 0)) {
			frm.set_df_property("posting_date", "read_only", 0);
		}
	},
});

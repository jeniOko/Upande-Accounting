// Bank Clearance: the Cheque / Reference No column can be corrected to what
// the bank shows. Changing it rewrites the reference on the Payment Entry /
// Journal Entry and reposts its ledger (upande_accounting/bank_clearance.py),
// so the user confirms those changes before they are applied.

const REFERENCE_EDITABLE = ["Payment Entry", "Journal Entry"];

frappe.ui.form.on("Bank Clearance", {
    refresh(frm) {
        if (!(frm.doc.payment_entries || []).length) return;

        // Replace ERPNext's button with one that confirms reference changes first
        frm.remove_custom_button(__("Update Clearance Date"));
        frm.add_custom_button(__("Update Clearance Date"), () => confirm_and_update(frm));
        frm.change_custom_button_type(__("Update Clearance Date"), null, "primary");
    },
});

function get_reference_changes(frm) {
    return (frm.doc.payment_entries || []).filter((row) => {
        const original = (row.custom_original_reference_no || "").trim();
        return (
            REFERENCE_EDITABLE.includes(row.payment_document) &&
            (row.cheque_number || "").trim() !== original
        );
    });
}

function confirm_and_update(frm) {
    const changes = get_reference_changes(frm);
    if (!changes.length) {
        update_clearance_date(frm);
        return;
    }

    const lines = changes
        .map(
            (row) =>
                `<li>${frappe.utils.escape_html(row.payment_entry)}: ` +
                `<s>${frappe.utils.escape_html(row.custom_original_reference_no || "-")}</s> &rarr; ` +
                `<b>${frappe.utils.escape_html((row.cheque_number || "").trim() || "-")}</b></li>`
        )
        .join("");

    frappe.confirm(
        __("These references will be changed on the voucher and its accounting ledger reposted:") +
            `<ul style="margin-top:8px">${lines}</ul>`,
        () => update_clearance_date(frm)
    );
}

function update_clearance_date(frm) {
    return frappe.call({
        method: "update_clearance_date",
        doc: frm.doc,
        freeze: true,
        callback: () => frm.refresh(),
    });
}

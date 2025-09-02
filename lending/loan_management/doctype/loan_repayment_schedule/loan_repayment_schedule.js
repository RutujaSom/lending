// Copyright (c) 2023, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Loan Repayment Schedule", {
	refresh(frm) {
    if (!frm.is_new()) {
        frm.add_custom_button('Import Loan Repayment Schedule', () => {
            open_import_dialog(frm);
        });
    }

	},
});


function open_import_dialog() {
    const d = new frappe.ui.Dialog({
        title: 'Import Loan Repayment Schedule',
        fields: [
            {
                label: 'File Type',
                fieldname: 'file_type',
                fieldtype: 'Select',
                options: ['CSV', 'Excel'],
                reqd: 1
            },
            {
                label: 'File',
                fieldname: 'file_url',
                fieldtype: 'Attach',
                reqd: 1
            }
        ],
        primary_action_label: 'Import',
        primary_action(values) {
            frappe.call({
				method: "lending.loan_management.doctype.loan_repayment_schedule.loan_repayment_schedule.bulk_update_repayment_dates",
                args: {
                    file_url: values.file_url,
                    file_type: values.file_type
                },
                callback(r) {
                    if (r.message) {
                        frappe.msgprint(r.message);
                        frappe.listview.refresh();  // refresh the list view
                    }
                    d.hide();
                }
            });
        }
    });

    d.show();
}

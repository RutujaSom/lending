// Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

lending.common.setup_filters("Loan Disbursement");

frappe.ui.form.on('Loan Disbursement', {
	setup(frm) {
		frm.ignore_doctypes_on_cancel_all = ["Loan Security Deposit", "Loan Repayment Schedule",
			"Sales Invoice", "Loan Interest Accrual", "Loan Demand", "Loan Restructure", "Loan Repayment", "Process Loan Classification"];
	},
	refresh: function(frm) {
		frm.set_query('against_loan', function() {
			return {
				'filters': {
					'docstatus': 1,
					"status": ["in",["Sanctioned","Active", "Partially Disbursed"]],
				}
			}
		})
		if (frm.doc.docstatus == 1 && frm.doc.repayment_schedule_type && frm.doc.status != "Closed") {
			frm.add_custom_button(__('Loan Repayment'), function() {
				frm.trigger("make_repayment_entry");
			},__('Create'));
		}
	},
	make_repayment_entry: function(frm) {
		frappe.call({
			args: {
				"loan": frm.doc.against_loan,
				"applicant_type": frm.doc.applicant_type,
				"applicant": frm.doc.applicant,
				"loan_product": frm.doc.loan_product,
				"company": frm.doc.company,
				"loan_disbursement": frm.doc.name,
				"as_dict": 1
			},
			method: "lending.loan_management.doctype.loan.loan.make_repayment_entry",
			callback: function (r) {
				if (r.message)
					var doc = frappe.model.sync(r.message)[0];
				frappe.set_route("Form", doc.doctype, doc.name);
			}
		})
	},
});





frappe.ui.form.on('Loan Disbursement', {
    refresh(frm) {
        if (frm.is_new()) {
            frm.add_custom_button('Import Loan Disbursement', () => {
                open_import_dialog(frm);
            });
        }
    }
});



function open_import_dialog() {
    const d = new frappe.ui.Dialog({
        title: 'Import Loan Disbursement',
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
				// method: "lending.loan_management.doctype.loan_disbursement.loan_disbursement.bulk_import_loan_disbursement",
				method: "lending.loan_management.doctype.loan_disbursement.loan_disbursement.import_and_submit_disbursement",
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



frappe.ui.form.on("Loan Disbursement", {
    against_loan: function(frm) {
        if (frm.doc.against_loan) {
            frappe.call({
                method: "frappe.client.get",
                args: {
                    doctype: "Loan",
                    name: frm.doc.against_loan
                },
                callback: function(r) {
                    if (r.message) {
                        let loan = r.message;
                        let loan_amount = loan.loan_amount || 0;
                        let total_charges = 0;

                        // ✅ Get company setting first
                        frappe.call({
                            method: "frappe.client.get_value",
                            args: {
                                doctype: "Company",
                                filters: { name: loan.company },
                                fieldname: ["deduct_charges_in_loan_disbursement"]
                            },
                            callback: function(cmp) {
                                if (cmp.message && cmp.message.deduct_charges_in_loan_disbursement) {
                                    // ✅ Deduct charges if enabled
                                    if (loan.loan_product) {
                                        frappe.call({
                                            method: "frappe.client.get",
                                            args: {
                                                doctype: "Loan Product",
                                                name: loan.loan_product
                                            },
                                            callback: function(res) {
                                                if (res.message) {
                                                    let charges = res.message.loan_charges || [];
                                                    charges.forEach(c => {
                                                        if (c.charge_based_on == 'Percentage') {
                                                            total_charges += (loan_amount * c.percentage) / 100;
                                                        } else {
                                                            total_charges += c.amount;
                                                        }
                                                    });

                                                    let net_amount = loan_amount - total_charges;
                                                    frm.set_value("disbursed_amount", net_amount);
                                                }
                                            }
                                        });
                                    }
                                } else {
                                    // ❌ No deduction → Disburse full loan amount
                                    frm.set_value("disbursed_amount", loan_amount);
                                }
                            }
                        });
                    }
                }
            });
        }
    },

    // Check if holiday exists on selected repayment date
    // Developer: Rutuja Somvanshi
    // Date: 25-09-2025
    repayment_start_date: function(frm) {
        if (frm.doc.repayment_start_date && frm.doc.against_loan) {
            frappe.db.get_doc("Loan", frm.doc.against_loan).then(loan => {
                frappe.call({
                    method: "lending.api.cust_disbursement.check_if_holiday",
                    args: {
                        company: loan.company,
                        repayment_date: frm.doc.repayment_start_date
                    },
                    callback: function(r) {
                        if (r.message && r.message.is_holiday) {
                            frappe.throw(`The selected Repayment Start Date <b>${frm.doc.repayment_start_date}</b> is a Holiday in <b>${r.message.holiday_list}</b>. Please choose another date.`);
                        }
                    }
                });
            });
        }
    }
});

# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.utils import (
	add_days,
	add_months,
	cint,
	date_diff,
	flt,
	get_datetime,
	get_last_day,
	getdate,
	nowdate,
)

import erpnext
from erpnext.accounts.general_ledger import make_gl_entries, process_gl_map
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.controllers.sales_and_purchase_return import make_return_doc

from lending.loan_management.doctype.loan.loan import get_cyclic_date
from lending.loan_management.doctype.loan_limit_change_log.loan_limit_change_log import (
	create_loan_limit_change_log,
)
from lending.loan_management.doctype.loan_repayment.loan_repayment import (
	get_pending_principal_amount,
)
from lending.loan_management.doctype.loan_repayment_schedule.utils import get_loan_partner_details
from lending.loan_management.doctype.loan_security_assignment.loan_security_assignment import (
	update_loan_securities_values,
)
from lending.loan_management.doctype.loan_security_release.loan_security_release import (
	get_pledged_security_qty,
)
from lending.loan_management.doctype.process_loan_interest_accrual.process_loan_interest_accrual import (
	process_loan_interest_accrual_for_loans,
)
from lending.api.cust_disbursement import check_if_holiday
from ex_loan_management.api.utils import get_paginated_data

# nosemgrep
class LoanDisbursement(AccountsController):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF
		from lending.loan_management.doctype.loan_disbursement_charge.loan_disbursement_charge import LoanDisbursementCharge

		against_loan: DF.Link
		amended_from: DF.Link | None
		applicant: DF.Link
		applicant_type: DF.Literal["Loan Member"]
		bank_account: DF.Link | None
		bpi_amount_difference: DF.Currency
		bpi_difference_date: DF.Date | None
		broken_period_interest: DF.Currency
		broken_period_interest_days: DF.Int
		clearance_date: DF.Date | None
		company: DF.Link
		cost_center: DF.Link | None
		current_disbursed_amount: DF.Currency
		days_past_due: DF.Int
		disbursed_amount: DF.Currency
		disbursement_account: DF.Link | None
		disbursement_date: DF.Date
		is_term_loan: DF.Check
		loan_account: DF.Link | None
		loan_disbursement_charges: DF.Table[LoanDisbursementCharge]
		loan_partner: DF.Link | None
		loan_product: DF.Link | None
		mode_of_payment: DF.Link | None
		monthly_repayment_amount: DF.Currency
		posting_date: DF.Date | None
		principal_amount_paid: DF.Currency
		reference_date: DF.Date | None
		reference_number: DF.Data | None
		refund_account: DF.Link | None
		repayment_days: DF.Int
		repayment_frequency: DF.Literal["Monthly", "Daily", "Weekly", "Bi-Weekly", "Quarterly", "One Time", "Custom"]
		repayment_method: DF.Literal["", "Repay Over Number of Periods", "Repay Fixed Amount per Period"]
		repayment_schedule_type: DF.Data | None
		repayment_start_date: DF.Date | None
		sanctioned_loan_amount: DF.Currency
		status: DF.Literal["", "Draft", "Submitted", "Cancelled", "Closed"]
		tenure: DF.Int
		withhold_security_deposit: DF.Check
	# end: auto-generated types

	def validate(self):
		self.set_status()
		self.set_missing_values()
		self.validate_disbursal_amount()
		if self.repayment_schedule_type == "Line of Credit":
			self.set_cyclic_date()

		self.validate_repayment_start_date()

		# Check if holiday exists on selected repayment date
		# Developer: Rutuja Somvanshi
		# Date: 25-09-2025
		check_if_holiday(self.company, self.repayment_start_date, from_validate=True)

	def on_update(self):
		if self.is_term_loan:
			self.make_update_draft_schedule()

	def on_trash(self):
		if self.docstatus == 0 and self.is_term_loan:
			draft_schedule = self.get_draft_schedule()
			frappe.delete_doc("Loan Repayment Schedule", draft_schedule)

	def get_schedule_details(self):
		return {
			"doctype": "Loan Repayment Schedule",
			"loan": self.against_loan,
			"repayment_method": self.repayment_method,
			"repayment_start_date": self.repayment_start_date,
			"repayment_periods": self.tenure,
			"posting_date": self.disbursement_date,
			"repayment_frequency": self.repayment_frequency,
			"disbursed_amount": self.disbursed_amount,
			# for amount on which schedules should be calcaulted 
			"current_principal_amount": self.sanctioned_loan_amount,
			"monthly_repayment_amount": self.monthly_repayment_amount
			if self.repayment_method == "Repay Fixed Amount per Period"
			else 0,
			"loan_disbursement": self.name,
		}

	def get_draft_schedule(self):
		draft_schedule = frappe.db.get_value(
			"Loan Repayment Schedule",
			{"loan": self.against_loan, "docstatus": 0, "loan_disbursement": self.name},
			"name",
		)
		return draft_schedule

	def make_update_draft_schedule(self):
		precision = cint(frappe.db.get_default("currency_precision")) or 2
		draft_schedule = self.get_draft_schedule()
		loan_product = frappe.db.get_value("Loan", self.against_loan, "loan_product")
		loan_details = frappe.db.get_value(
			"Loan", self.against_loan, ["repayment_periods", "moratorium_tenure", "status"], as_dict=1
		)

		if self.repayment_frequency == "Monthly" and not self.repayment_start_date:
			self.repayment_start_date = get_cyclic_date(loan_product, self.posting_date)

			if loan_details.status == "Sanctioned" and loan_details.moratorium_tenure:
				self.repayment_start_date = add_months(
					self.repayment_start_date, loan_details.moratorium_tenure
				)

		if draft_schedule:
			schedule = frappe.get_doc("Loan Repayment Schedule", draft_schedule)
			schedule.update(self.get_schedule_details())
			schedule.save()
		else:
			schedule = frappe.get_doc(self.get_schedule_details()).insert()

		self.db_set("monthly_repayment_amount", schedule.monthly_repayment_amount)
		if loan_details.status == "Sanctioned":
			self.db_set("broken_period_interest", flt(schedule.broken_period_interest, precision))
			self.db_set("broken_period_interest_days", flt(schedule.broken_period_interest_days, precision))

	def on_submit(self):
		if self.is_term_loan:
			loan_status = frappe.db.get_value("Loan", self.against_loan, "status")
			if loan_status == "Partially Disbursed":
				process_loan_interest_accrual_for_loans(
					posting_date=self.disbursement_date, loan=self.against_loan, accrual_type="Disbursement"
				)

			self.submit_repayment_schedule()
			self.update_current_repayment_schedule()
			self.update_repayment_schedule_status()
		self.set_status_and_amounts()

		update_loan_securities_values(self.against_loan, self.disbursed_amount, self.doctype)
		self.create_loan_limit_change_log()
		self.withheld_security_deposit()
		self.make_gl_entries()

	def set_status(self):
		if self.docstatus == 0:
			self.status = "Draft"
		elif self.docstatus == 1:
			self.db_set("status", "Submitted")
		elif self.docstatus == 2:
			self.db_set("status", "Cancelled")

	def add_bpi_difference_entry(self, gle_map):
		if flt(self.bpi_amount_difference) > 0:
			broken_period_interest_account = frappe.db.get_value(
				"Loan Product", self.loan_product, "broken_period_interest_recovery_account"
			)
			if not broken_period_interest_account:
				frappe.throw(
					_("Please set Broken Period Interest Recovery Account for the Loan Product {0}").format(
						frappe.bold(self.loan_product)
					)
				)
			self.add_gl_entry(
				gle_map,
				broken_period_interest_account,
				self.disbursement_account,
				-1 * self.bpi_amount_difference,
				_("BPI difference entry"),
				bpi_difference_date=self.bpi_difference_date,
			)

	def submit_repayment_schedule(self):
		filters = {
			"loan": self.against_loan,
			"docstatus": 0,
			"status": "Initiated",
			"loan_disbursement": self.name,
		}
		schedule = frappe.get_doc("Loan Repayment Schedule", filters)
		schedule.submit()

	def cancel_and_delete_repayment_schedule(self):
		filters = {
			"loan": self.against_loan,
			"docstatus": 1,
			"loan_disbursement": self.name,
		}
		schedule = frappe.get_doc("Loan Repayment Schedule", filters)
		schedule.reverse_interest_accruals = self.get("reverse_interest_accruals")
		schedule.cancel()

	def make_credit_note(self):
		filters = {
			"loan": self.against_loan,
			"loan_disbursement": self.name,
			"docstatus": 1,
		}

		for si in frappe.get_all("Sales Invoice", filters, pluck="name"):
			doc = make_return_doc("Sales Invoice", si)
			doc.update_outstanding_for_self = 0
			doc.loan_disbursement = ""

			items_to_remove = []
			if self.get("reverse_charges"):
				for item in doc.get("items"):
					if item.item_code not in self.get("reverse_charges"):
						items_to_remove.append(item)

			if items_to_remove:
				for item in items_to_remove:
					doc.remove(item)

			doc.save()
			doc.submit()
			doc.set_status(update=True)

	def update_current_repayment_schedule(self, cancel=0):
		# Update status of existing schedule on top up
		if cancel:
			status = "Active"
			current_status = "Outdated"
		else:
			status = "Outdated"
			current_status = "Active"

		if self.repayment_schedule_type != "Line of Credit":
			existing_schedule = frappe.db.get_value(
				"Loan Repayment Schedule",
				{"loan": self.against_loan, "docstatus": 1, "status": current_status},
			)

			if existing_schedule:
				frappe.db.set_value("Loan Repayment Schedule", existing_schedule, "status", status)

	def update_repayment_schedule_status(self, cancel=0):
		if cancel:
			status = "Initiated"
			current_status = "Active"
		else:
			status = "Active"
			current_status = "Initiated"

		filters = {"loan": self.against_loan, "docstatus": 1, "status": current_status}
		schedule = frappe.db.get_value(
			"Loan Repayment Schedule",
			filters,
			"name",
		)
		frappe.db.set_value("Loan Repayment Schedule", schedule, "status", status)

	def on_cancel(self):
		self.flags.ignore_links = ["GL Entry", "Loan Repayment Schedule", "Sales Invoice", "Loan Demand"]

		self.set_status_and_amounts(cancel=1)

		if self.is_term_loan:
			self.cancel_and_delete_repayment_schedule()

		self.make_credit_note()
		self.delete_security_deposit()

		update_loan_securities_values(
			self.against_loan,
			self.disbursed_amount,
			self.doctype,
			on_trigger_doc_cancel=1,
		)

		self.make_gl_entries(cancel=1)
		self.ignore_linked_doctypes = ["GL Entry", "Payment Ledger Entry"]
		self.set_status()

	def set_missing_values(self):
		if not self.disbursement_date:
			self.disbursement_date = nowdate()

		self.posting_date = nowdate()

		if not self.cost_center:
			self.cost_center = erpnext.get_default_cost_center(self.company)

		if not self.disbursement_account and self.bank_account:
			self.disbursement_account = frappe.db.get_value("Bank Account", self.bank_account, "account")

		if self.mode_of_payment:
			self.disbursement_account = frappe.db.get_value(
				"Mode of Payment Account",
				{"parent": self.mode_of_payment, "company": self.company},
				"default_account",
			)

		if self.repayment_method == "Repay Fixed Amount per Period":
			self.monthly_repayment_amount = frappe.db.get_value(
				"Loan", self.against_loan, "monthly_repayment_amount"
			)

	def withheld_security_deposit(self):
		if self.withhold_security_deposit:
			sd = frappe.get_doc(
				{
					"doctype": "Loan Security Deposit",
					"loan": self.against_loan,
					"loan_disbursement": self.name,
					"deposit_amount": self.monthly_repayment_amount,
					"available_amount": self.monthly_repayment_amount,
				}
			).insert()
			sd.submit()

	def set_cyclic_date(self):
		if self.repayment_frequency == "Monthly" and not self.repayment_start_date:
			cycle_day, min_days_bw_disbursement_first_repayment = frappe.db.get_value(
				"Loan Product",
				self.loan_product,
				["cyclic_day_of_the_month", "min_days_bw_disbursement_first_repayment"],
			)
			cycle_day = cint(cycle_day)

			last_day_of_month = get_last_day(self.posting_date)
			cyclic_date = add_days(last_day_of_month, cycle_day)

			broken_period_days = date_diff(cyclic_date, self.posting_date)
			if broken_period_days < min_days_bw_disbursement_first_repayment:
				cyclic_date = add_days(get_last_day(cyclic_date), cycle_day)

			self.repayment_start_date = cyclic_date

	def delete_security_deposit(self):
		if self.withhold_security_deposit:
			sd = frappe.get_doc("Loan Security Deposit", {"loan_disbursement": self.name})
			sd.cancel()
			sd.delete()

	def validate_repayment_start_date(self):
		if self.repayment_start_date and getdate(self.repayment_start_date) < getdate(
			self.disbursement_date
		):
			frappe.throw(_("Repayment Start Date cannot be before Disbursement Date"))

	def validate_disbursal_amount(self):
		possible_disbursal_amount, pending_principal_amount = get_disbursal_amount(self.against_loan)
		limit_details = frappe.db.get_value(
			"Loan",
			self.against_loan,
			[
				"limit_applicable_start",
				"limit_applicable_end",
				"available_limit_amount",
			],
			as_dict=1,
		)
		print('possible_disbursal_amount ....',possible_disbursal_amount)
		print('self.disbursed_amount ....',self.disbursed_amount)

		if not self.disbursed_amount:
			frappe.throw(_("Disbursed amount cannot be zero"))
		elif (
			self.disbursed_amount > possible_disbursal_amount
			and self.repayment_schedule_type != "Line of Credit"
		):
			frappe.throw(_("Disbursed Amount cannot be greater than {0}").format(possible_disbursal_amount))
		elif self.repayment_schedule_type == "Line of Credit":
			if not (
				getdate(limit_details.limit_applicable_start)
				<= getdate(self.disbursement_date)
				<= getdate(limit_details.limit_applicable_end)
			):
				frappe.throw(_("Disbursement date is out of approved limit dates"))

		if limit_details.available_limit_amount and self.disbursed_amount > flt(
			limit_details.available_limit_amount
		):
			frappe.throw(_("Disbursement amount cannot be greater than available limit amount"))

	def create_loan_limit_change_log(self):
		if self.repayment_schedule_type == "Line of Credit":
			create_loan_limit_change_log(
				loan=self.against_loan,
				event="Disbursement",
				change_date=self.disbursement_date,
				value_type="Utilized Limit Amount",
				value_change=self.disbursed_amount,
			)

	def set_status_and_amounts(self, cancel=0):
		loan_details = frappe.get_all(
			"Loan",
			fields=[
				"loan_amount",
				"disbursed_amount",
				"total_payment",
				"total_principal_paid",
				"total_interest_payable",
				"repayment_schedule_type",
				"status",
				"is_term_loan",
				"is_secured_loan",
				"maximum_limit_amount",
				"available_limit_amount",
				"utilized_limit_amount",
			],
			filters={"name": self.against_loan},
		)[0]

		if cancel:
			(
				disbursed_amount,
				status,
				total_payment,
				total_interest_payable,
				new_available_limit_amount,
				new_utilized_limit_amount,
			) = self.get_values_on_cancel(loan_details)
		else:
			(
				disbursed_amount,
				status,
				total_payment,
				total_interest_payable,
				new_available_limit_amount,
				new_utilized_limit_amount,
			) = self.get_values_on_submit(loan_details)

		frappe.db.set_value(
			"Loan",
			self.against_loan,
			{
				"disbursement_date": self.disbursement_date,
				"disbursed_amount": disbursed_amount,
				"status": status,
				"total_payment": total_payment,
				"total_interest_payable": total_interest_payable,
				"available_limit_amount": new_available_limit_amount,
				"utilized_limit_amount": new_utilized_limit_amount,
			},
		)

	def get_values_on_cancel(self, loan_details):
		disbursed_amount = loan_details.disbursed_amount - self.disbursed_amount
		total_payment = loan_details.total_payment
		total_interest_payable = loan_details.total_interest_payable

		if self.is_term_loan:
			schedule = frappe.get_doc(
				"Loan Repayment Schedule", {"loan_disbursement": self.name, "docstatus": 1}
			)
			for data in schedule.repayment_schedule:
				total_payment -= data.total_payment
				total_interest_payable -= data.interest_amount
		else:
			total_payment -= self.disbursed_amount

		if (
			loan_details.disbursed_amount > loan_details.loan_amount
			and loan_details.repayment_schedule_type != "Line of Credit"
		):
			topup_amount = loan_details.disbursed_amount - loan_details.loan_amount
			if topup_amount > self.disbursed_amount:
				topup_amount = self.disbursed_amount

			total_payment = total_payment - topup_amount

		if loan_details.repayment_schedule_type == "Line of Credit":
			status = "Active"
		elif disbursed_amount <= 0:
			status = "Sanctioned"
		elif disbursed_amount >= loan_details.loan_amount:
			status = "Disbursed"
		else:
			status = "Partially Disbursed"

		new_available_limit_amount = loan_details.available_limit_amount + self.disbursed_amount

		new_utilized_limit_amount = loan_details.utilized_limit_amount - self.disbursed_amount

		return (
			disbursed_amount,
			status,
			total_payment,
			total_interest_payable,
			new_available_limit_amount,
			new_utilized_limit_amount,
		)

	def get_values_on_submit(self, loan_details):
		precision = cint(frappe.db.get_default("currency_precision")) or 2
		disbursed_amount = self.disbursed_amount + loan_details.disbursed_amount

		if loan_details.repayment_schedule_type == "Line of Credit":
			total_payment = loan_details.total_payment
			total_interest_payable = loan_details.total_interest_payable
		else:
			total_payment = 0
			total_interest_payable = 0

		if loan_details.status in ("Disbursed", "Partially Disbursed") and not loan_details.is_term_loan:
			process_loan_interest_accrual_for_loans(
				posting_date=add_days(self.disbursement_date, -1),
				loan=self.against_loan,
				accrual_type="Disbursement",
			)

		if self.is_term_loan:
			schedule = frappe.get_doc("Loan Repayment Schedule", {"loan_disbursement": self.name})
			for data in schedule.repayment_schedule:
				if getdate(data.payment_date) >= getdate(self.repayment_start_date):
					total_payment += flt(data.total_payment, precision)
					total_interest_payable += flt(data.interest_amount, precision)
		else:
			total_payment = self.disbursed_amount

		if disbursed_amount > loan_details.loan_amount:
			topup_amount = disbursed_amount - loan_details.loan_amount

			if topup_amount < 0:
				topup_amount = 0

			if topup_amount > self.disbursed_amount:
				topup_amount = self.disbursed_amount

		if self.repayment_schedule_type == "Line of Credit":
			status = "Active"
		elif flt(disbursed_amount) >= loan_details.loan_amount:
			status = "Disbursed"
		else:
			status = "Partially Disbursed"

		new_available_limit_amount = (
			loan_details.available_limit_amount - self.disbursed_amount
			if loan_details.maximum_limit_amount
			else 0.0
		)
		new_utilized_limit_amount = (
			loan_details.utilized_limit_amount + self.disbursed_amount
			if loan_details.maximum_limit_amount
			else 0.0
		)

		return (
			disbursed_amount,
			status,
			total_payment,
			total_interest_payable,
			new_available_limit_amount,
			new_utilized_limit_amount,
		)

	def add_gl_entry(
		self,
		gl_entries,
		account,
		against_account,
		amount,
		remarks=None,
		against_voucher_type=None,
		against_voucher=None,
		bpi_difference_date=None,
	):
		account_type = frappe.db.get_value("Account", account, "account_type")
		gl_entries.append(
			self.get_gl_dict(
				{
					"account": account,
					"against": against_account,
					"debit": amount,
					"debit_in_account_currency": amount,
					"against_voucher_type": against_voucher_type or "Loan",
					"against_voucher": against_voucher or self.against_loan,
					"remarks": remarks,
					"cost_center": self.cost_center,
					"party_type": self.applicant_type if account_type in ("Receivable", "Payable") else None,
					"party": self.applicant if account_type in ("Receivable", "Payable") else None,
					"posting_date": self.posting_date,
				}
			)
		)
		account_type = frappe.db.get_value("Account", against_account, "account_type")
		gl_entries.append(
			self.get_gl_dict(
				{
					"account": against_account,
					"against": account,
					"debit": -1 * amount,
					"debit_in_account_currency": -1 * amount,
					"against_voucher_type": "Loan",
					"against_voucher": self.against_loan,
					"remarks": remarks,
					"party_type": self.applicant_type if account_type in ("Receivable", "Payable") else None,
					"party": self.applicant if account_type in ("Receivable", "Payable") else None,
					"cost_center": self.cost_center,
					"posting_date": self.posting_date,
				}
			)
		)

	def make_gl_entries(self, cancel=0, adv_adj=0, repost=0):
		gle_map = []
		remarks = _("Disbursement against loan:") + self.against_loan

		precision = cint(frappe.db.get_default("currency_precision")) or 2

		if self.get("refund_account") and cancel:
			bank_account = self.refund_account
		else:
			bank_account = self.disbursement_account

		self.add_gl_entry(gle_map, self.loan_account, bank_account, self.disbursed_amount, remarks)

		if self.withhold_security_deposit:
			security_deposit_account = frappe.db.get_value(
				"Loan Product", self.loan_product, "security_deposit_account"
			)

			self.add_gl_entry(
				gle_map,
				security_deposit_account,
				bank_account,
				-1 * self.monthly_repayment_amount,
				remarks,
			)

		if self.broken_period_interest:
			broken_period_interest_account = frappe.db.get_value(
				"Loan Product", self.loan_product, "broken_period_interest_recovery_account"
			)

			if not broken_period_interest_account:
				frappe.throw(
					_("Please set Broken Period Interest Recovery Account for the Loan Product {0}").format(
						frappe.bold(self.loan_product)
					)
				)

			self.add_gl_entry(
				gle_map,
				broken_period_interest_account,
				bank_account,
				flt(-1 * self.broken_period_interest, precision),
				remarks,
			)

		if self.get("loan_disbursement_charges") and not cancel and not repost:
			make_sales_invoice_for_charge(
				self.against_loan,
				"loan_disbursement",
				self.name,
				self.applicant if self.applicant_type == "Customer" else None,
				self.disbursement_date,
				self.company,
				self.get("loan_disbursement_charges"),
			)

		filters = {"loan": self.against_loan, "docstatus": 1, "is_return": 0}
		if cancel:
			filters["is_return"] = 1
		else:
			filters["loan_disbursement"] = self.name

		sales_invoices = frappe.db.get_all(
			"Sales Invoice",
			filters=filters,
			fields=["name", "debit_to", "grand_total"],
		)

		for invoice in sales_invoices:
			self.add_gl_entry(
				gle_map,
				invoice.debit_to,
				bank_account,
				-1 * abs(invoice.grand_total),
				remarks,
				"Sales Invoice",
				invoice.name,
			)

		if self.loan_partner:
			loan_partner_details = get_loan_partner_details(self.loan_partner)
			if loan_partner_details.enable_partner_accounting:
				self.add_gl_entry(
					gle_map,
					loan_partner_details.receivable_account,
					loan_partner_details.credit_account,
					(self.disbursed_amount * loan_partner_details.partner_loan_share_percentage) / 100,
					remarks,
				)

		self.add_bpi_difference_entry(gle_map)

		if gle_map:
			if cancel:
				gle_map = process_gl_map(gle_map)

			make_gl_entries(gle_map, cancel=cancel, adv_adj=adv_adj)


def make_sales_invoice_for_charge(
	loan, reference_fieldname, reference_doctype, applicant, disbursement_date, company, charges
):
	if not charges:
		return

	si = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"customer": applicant,
			"loan": loan,
			reference_fieldname: reference_doctype,
			"set_posting_time": 1,
			"posting_date": disbursement_date,
			"due_date": disbursement_date,
			"company": company,
			"conversion_rate": 1,
		}
	)

	si.against_voucher_type = "Loan"
	si.against_voucher = loan

	loan_product = frappe.db.get_value("Loan", loan, "loan_product")

	for charge in charges:
		account = frappe.db.get_value(
			"Loan Charges", {"parent": loan_product, "charge_type": charge.charge}, "income_account"
		)
		receivable_account = charge.get("account")
		if not account:
			account = frappe.db.get_value(
				"Item Default", {"parent": charge.charge, "company": company}, "income_account"
			)

		si.append(
			"items",
			{"item_code": charge.charge, "rate": charge.amount, "qty": 1, "income_account": account},
		)

	if reference_doctype == "Loan Disbursement":
		si.debit_to = receivable_account
	si.ignore_default_payment_terms_template = 1

	si.save()
	si.submit()

	return si


def get_total_pledged_security_value(loan):
	update_time = get_datetime()

	loan_security_price_map = frappe._dict(
		frappe.get_all(
			"Loan Security Price",
			fields=["loan_security", "loan_security_price"],
			filters={"valid_from": ("<=", update_time), "valid_upto": (">=", update_time)},
			as_list=1,
		)
	)

	hair_cut_map = frappe._dict(
		frappe.get_all("Loan Security", fields=["name", "haircut"], as_list=1)
	)

	security_value = 0.0
	pledged_securities = get_pledged_security_qty(loan)

	for security, qty in pledged_securities.items():
		after_haircut_percentage = 100 - hair_cut_map.get(security)
		security_value += (
			loan_security_price_map.get(security, 0) * qty * after_haircut_percentage
		) / 100

	return security_value


@frappe.whitelist()
def get_disbursal_amount(loan, on_current_security_price=0):
	loan_details = frappe.get_value(
		"Loan",
		loan,
		[
			'name',
			"loan_amount",
			"disbursed_amount",
			"total_payment",
			"debit_adjustment_amount",
			"credit_adjustment_amount",
			"refund_amount",
			"total_principal_paid",
			"total_interest_payable",
			"status",
			"is_term_loan",
			"is_secured_loan",
			"maximum_loan_amount",
			"written_off_amount",
		],
		as_dict=1,
		for_update=True,
	)

	if loan_details.is_secured_loan and frappe.get_all(
		"Loan Security Shortfall", filters={"loan": loan, "status": "Pending"}
	):
		return 0

	print('loan_details ....',loan_details)
	pending_principal_amount = get_pending_principal_amount(loan_details)

	security_value = 0.0
	if loan_details.is_secured_loan and on_current_security_price:
		security_value = get_total_pledged_security_value(loan)

	if loan_details.is_secured_loan and not on_current_security_price:
		security_value = get_maximum_amount_as_per_pledged_security(loan)
		print('security_value .....,,,',security_value)

	if not security_value and not loan_details.is_secured_loan:
		print('in if ....')
		security_value = flt(loan_details.loan_amount)

	disbursal_amount = flt(security_value) - flt(pending_principal_amount)
	print('disbursal_amount ...',disbursal_amount,'pending_principal_amount ...',pending_principal_amount)

	if (
		loan_details.is_term_loan
		and (disbursal_amount + loan_details.loan_amount) > loan_details.loan_amount
	):
		print('loan_details.loan_amount ...',loan_details.loan_amount)
		disbursal_amount = loan_details.loan_amount - loan_details.disbursed_amount

	return disbursal_amount, pending_principal_amount


def get_maximum_amount_as_per_pledged_security(loan):
	return flt(
		frappe.db.get_value("Loan Security Assignment", {"loan": loan}, [{"SUM": "maximum_loan_value"}])
	)

























# ---------------------------------------------------------------



import frappe
import pandas as pd
from datetime import datetime

@frappe.whitelist()
def bulk_import_loan_disbursement(file_url):
    print('bulk_import_loan_disbursement ....')
    # Get File doc
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    file_path = file_doc.get_full_path()

    # Read file with pandas
    if file_url.endswith(".csv"):
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)

    success = []
    errors = []

    for idx, row in df.iterrows():
        try:
            print('loan .....',row.get("LOAN ID"))
            # --- Find Loan Application ---
            loan_name = frappe.get_doc("Loan", {"loan_id": row.get("LOAN ID")})
            if not loan_name:
                raise Exception(f"Loan '{row.get('AGAINST LOAN')}' not found")
            print('loan_name .....',loan_name)

            # --- Parse Dates ---
            disbursement_date = row.get("LOAN SANCTION DATE/ trasancation date")
            if isinstance(disbursement_date, str):
                for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
                    try:
                        disbursement_date = datetime.strptime(disbursement_date, fmt).date()
                        break
                    except:
                        continue

            # --- Get Applicant ---
            print('row.get("MEMBER NO") ...',row.get("MEMBER NO"))
            applicant = frappe.get_value("Loan Member", {"member_id": row.get("MEMBER NO")}, "name")
            if not applicant:
                raise Exception(f"Applicant '{row.get('MEMBER NO')}' not found")


            # --- Prepare Data ---
            disbursement_data = {
                "doctype": "Loan Disbursement",
                "against_loan": loan_name.name,
                "disbursement_date": disbursement_date,
                "disbursed_amount": row.get("LOAN AMOUNT"),
                "mode_of_payment": row.get("MODE OF PAYMENT"),
                "disbursement_account": row.get("DISBURSEMENT ACCOUNT"),
                "loan_account": row.get("LOAN ACCOUNT"),
                "company": "Excellminds (Demo)",
                "reference_number": row.get("REFERENCE NO"),
                "reference_date": row.get("REFERENCE DATE"),
				"applicant_type":"LOAN MEMBER",
				"applicant":applicant,
				"sanctioned_loan_amount":loan_name.loan_amount,
				"current_disbursed_amount":loan_name.disbursed_amount,
				"monthly_repayment_amount":loan_name.monthly_repayment_amount,

            }

            # --- Create & Submit Loan Disbursement ---
            disb_doc = frappe.get_doc(disbursement_data)
            disb_doc.insert()
            # disb_doc.submit()
            print('disb_doc ...',disb_doc)

            success.append(disb_doc.name)

        except Exception as e:
            # return f"e ....{e}"
            frappe.log_error(f"Bulk Loan Disbursement Error (Row {idx+1}): {str(e)}")
            errors.append(f"Row {idx+1}: {str(e)}")

    return f"success_count: {len(success)}, error_count: {len(errors)}"







@frappe.whitelist()
def import_and_submit_disbursement(file_url):
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    file_path = file_doc.get_full_path()

    # Read file with pandas
    if file_url.endswith(".csv"):
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)
	# ✅ Keep only EMI No = 1 records for each Loan ID
    df = df[df["EMI NO"] == 1]
    print('df ...',df)

    success, errors = [], []

    for _, row in df.iterrows():
        try:
            loan_id = row.get("LOAN ID")
            repay_start = row.get("EMI DATE")

            print('loan .....',row.get("LOAN ID"))
			# --- Find Loan Application ---
            loan_name = frappe.get_doc("Loan", {"loan_id": row.get("LOAN ID")})
            if not loan_name:
                raise Exception(f"Loan '{row.get('AGAINST LOAN')}' not found")
            print('loan_name .....',loan_name)


            if not loan_id or not repay_start:
                raise Exception("Loan ID and Repayment Start Date are required")
            print('before ...')
            # --- Find matching Loan Disbursement (Draft only) ---
            disb_name = frappe.db.get_value(
                "Loan Disbursement",
                {
                    # "loan": loan_id,
					"against_loan": loan_name.name,
                    # "repayment_start_date": repay_start,
                    "docstatus": 0
                },
                "name"
            )
            print('disb_name ...',disb_name)

            if not disb_name:
                raise Exception(f"No Draft Loan Disbursement found for Loan {loan_id} with Repayment Start {repay_start}")

            # --- Load and Submit ---
            disb_doc = frappe.get_doc("Loan Disbursement", disb_name)
            disb_doc.submit()

            success.append({
                # "against_loan": loan_name.name,
                "repayment_start_date": repay_start,
                "disbursement": disb_name,
                "status": "Submitted"
            })

        except Exception as e:
            # return f"e ....,{e}"
            frappe.log_error(f"Bulk Loan Disbursement Error (Row: {str(e)}")
            errors.append({"row": dict(row), "error": str(e)})

    return f"success_count: {len(success)}, error_count: {len(errors)}"





update_fields = [
    "name",
    "against_loan",
	"sanctioned_loan_amount",
	"current_disbursed_amount",
	"posting_date",
	"applicant_type",
	"loan_product",
	"monthly_repayment_amount",
	"loan_partner",
	"company",
	"applicant",
	"repayment_schedule_type",
	"repayment_frequency",
	"repayment_method",
	"tenure",
	"repayment_start_date",
	"is_term_loan",
	"withhold_security_deposit",
	"repayment_days",
	"disbursement_date",
	"clearance_date",
	"bpi_difference_date",
	"broken_period_interest_days",
	"disbursed_amount",
	"broken_period_interest",
	"bpi_amount_difference",
	"principal_amount_paid",
	"mode_of_payment",
	"disbursement_account",
	"refund_account",
	"loan_account",
	"bank_account",
	"cost_center",
	"reference_date",
	"days_past_due",
	"status",
	"reference_number",
	"amended_from",
]

"""
	Get Loan Disbursement List (with optional pagination, search & sorting)
"""
@frappe.whitelist()
def loan_disbursement_list(page=1, page_size=10, search=None, sort_by="name", sort_order="asc", is_pagination=False,**kwargs):
    is_pagination = frappe.utils.sbool(is_pagination)  # convert "true"/"false"/1/0 into bool
    extra_params = {"search": search} if search else {}
    if "cmd" in kwargs:
        del kwargs["cmd"]

    # 🔹 Collect filters from kwargs (all query params except the defaults)
    filters = {}
    for k, v in kwargs.items():
        if v not in [None, ""]:   # skip empty params
            filters[k] = v

    base_url = frappe.request.host_url.rstrip("/") + frappe.request.path
    parent_data =  get_paginated_data(
        doctype="Loan Disbursement",
        fields=update_fields,
        filters=filters,   # ✅ Now includes applicant filter if loan_group provided
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        page=int(page),
        page_size=int(page_size),
        search_fields=["name"],
        is_pagination=is_pagination,
        base_url=base_url,
        extra_params=extra_params,
		link_fields ={"applicant":"member_name"}
    )

    return parent_data
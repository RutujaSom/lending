# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe import _
import frappe
from frappe.utils import getdate


class LoanGroupAssignment(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		employee: DF.Link
		end_date: DF.Date | None
		loan_group: DF.Link
		start_date: DF.Date
	# end: auto-generated types

	def validate(self):
		self.validate_group_assignment()

	def validate_group_assignment(self):
		# Get existing active assignments for the same loan group
		print('self.loan_group .....',self.loan_group)
		existing_assignments = frappe.get_all(
			"Loan Group Assignment",
			filters={"loan_group": self.loan_group,  "employee": self.employee, "name": ["!=", self.name]},
			fields=["employee", "start_date", "end_date"]
		)

		for assignment in existing_assignments:
			group_name = frappe.db.get_value("Loan Group", self.loan_group, "group_name") or self.loan_group
			employee_name = frappe.db.get_value("Employee", self.employee, "employee_name") or self.employee
			if not assignment.end_date:
				# Already mapped without end_date
				frappe.throw(
					_("Loan Group '{0}' is already assigned to '{1}' without an End Date. Please close it first.")
					.format(group_name, employee_name)
				)

			elif assignment.end_date and getdate(self.start_date) <= assignment.end_date:
				# Overlapping dates
				frappe.throw(
					_("Loan Group '{0}' was already assigned to '{1}' until {2}. Start Date must be after {2}.")
					.format(group_name, employee_name, assignment.end_date)
				)




# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


# import frappe
from frappe.model.document import Document


class RepaymentSchedule(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		balance_loan_amount: DF.Currency
		demand_generated: DF.Check
		interest_amount: DF.Currency
		number_of_days: DF.Int
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		payment_date: DF.Date | None
		principal_amount: DF.Currency
		total_payment: DF.Currency
	# end: auto-generated types

	pass


import frappe
from frappe.utils import today, nowdate

@frappe.whitelist(allow_guest=False)
def get_todays_emis(
    selected_date=None, 
    search_text=None, 
    sort_by="rs.payment_date", 
    sort_order="ASC", 
    employee=None,
    loan_group=None,
    is_pagination=False,
    upto_date=None,
    applicant=None,
    is_schedular=False
):
    print("Selected Date:", selected_date, type(selected_date))
    if not upto_date:
        upto_date = today()

    # Validate sort_by to prevent SQL injection
    allowed_sort_fields = [
        "rs.payment_date", "lm.member_name", "lrs.loan", 
        "rs.total_payment", "rs.balance_loan_amount"
    ]
    if sort_by not in allowed_sort_fields:
        sort_by = "rs.payment_date"

    sort_order = "ASC" if sort_order.upper() == "ASC" else "DESC"

    if selected_date:
        conditions = "WHERE rs.payment_date = %s and rs.balance_loan_amount > 0"
        params = [selected_date]

    else:
        conditions = "WHERE rs.payment_date <= %s and rs.balance_loan_amount > 0"
        params = [upto_date]


    if loan_group:
        conditions += " AND lm.group LIKE %s"
        params.extend([f"%{loan_group}%"])

    if applicant:
        conditions += " AND l.applicant = %s"
        params.extend([f"{applicant}"])

    # Add dynamic search (searching applicant name or loan number)
    if search_text:
        conditions += " AND (lm.member_name LIKE %s OR lm.member_id LIKE %s OR lrs.loan LIKE %s)"
        params.extend([f"%{search_text}%", f"%{search_text}%",f"%{search_text}%"])

    user = frappe.session.user
    roles = frappe.get_roles(user) 
    if not is_schedular:
        # Case 1: Logged in as Employee
        if not any(role in roles for role in ["Administrator", "System Manager", "Accounts Manager","Loan Manager"]):
            employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
            if employee:
                assigned_groups = _get_active_groups(employee)
                if assigned_groups:
                    conditions += " AND lm.group IN %s"
                    params.append(tuple(assigned_groups))
                else:
                    return []

        # Case 2: Logged in as Admin → optional filter by employee
        else:
            if employee:
                employee = frappe.db.get_value("Employee", {"name": employee}, "name")
                if employee:
                    assigned_groups = _get_active_groups(employee)
                    if assigned_groups:
                        conditions += " AND lm.group IN %s"
                        params.append(tuple(assigned_groups))
                    else:
                        return []
                
    host_url = frappe.request.host_url.rstrip("/")

    query = f"""
        SELECT 
            rs.parent as loan_repayment_schedule,
            rs.payment_date,
            rs.principal_amount,
            rs.interest_amount,
            rs.total_payment,
            rs.balance_loan_amount,
            lrs.loan,
            l.applicant_type,
            l.applicant,
            l.loan_id,
            lm.member_name,
            lm.group,
            lm.mobile_no,
            lm.mobile_no_2,
            CONCAT('{host_url}', lm.member_image) as member_image,
            COALESCE(SUM(lr.amount_paid), 0) as amount_paid,
            CASE 
                WHEN COALESCE(SUM(lr.amount_paid), 0) >= rs.total_payment THEN 'Done ✅'
                WHEN COALESCE(SUM(lr.amount_paid), 0) > 0 AND COALESCE(SUM(lr.amount_paid), 0) < rs.total_payment THEN 'Partial ⚠️'
                ELSE 'Pending ❌'
            END as payment_status,
            CASE 
                WHEN COALESCE(SUM(lr.amount_paid), 0) >= rs.total_payment THEN 0
                ELSE rs.total_payment - COALESCE(SUM(lr.amount_paid), 0)
            END as remaining_amount
        FROM `tabRepayment Schedule` rs
        LEFT JOIN `tabLoan Repayment Schedule` lrs ON rs.parent = lrs.name
        LEFT JOIN `tabLoan` l ON l.name = lrs.loan
        LEFT JOIN `tabLoan Member` lm 
            ON (l.applicant_type = 'Loan Member' AND l.applicant = lm.name)
        LEFT JOIN `tabLoan Repayment` lr 
            ON lr.against_loan = lrs.loan 
            AND DATE(lr.value_date) = rs.payment_date 
            AND lr.workflow_state IN ('Approved', 'Pending', 'Open')
        {conditions}
        AND l.status IN (
            'Sanctioned',
            'Partially Disbursed',
            'Disbursed',
            'Active',
            'Loan Closure Requested'
        )
        GROUP BY rs.name
        HAVING COALESCE(SUM(lr.amount_paid), 0) < rs.total_payment
        ORDER BY {sort_by} {sort_order}
    """

    emis = frappe.db.sql(query, tuple(params), as_dict=True)
    return emis


def _get_active_groups(employee):
    """Return active loan groups assigned to an employee today"""
    groups = frappe.get_all(
        "Loan Group Assignment",
        filters={
            "employee": employee,
            "start_date": ("<=", nowdate()),
            "end_date": ("in", ["", None])  # open-ended
        },
        pluck="loan_group"
    )
    extra = frappe.get_all(
        "Loan Group Assignment",
        filters={
            "employee": employee,
            "start_date": ("<=", nowdate()),
            "end_date": (">=", nowdate())
        },
        pluck="loan_group"
    )
    return list(set(groups + extra))

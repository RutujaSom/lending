# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt


import json
import math

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.utils import cint, flt, rounded

from lending.loan_management.doctype.loan.loan import (
	get_sanctioned_amount_limit,
	get_total_loan_amount,
)
from lending.loan_management.doctype.loan_repayment_schedule.loan_repayment_schedule import (
	get_monthly_repayment_amount,
)
from lending.loan_management.doctype.loan_security_price.loan_security_price import (
	get_loan_security_price,
)
import frappe
import pandas as pd
from datetime import datetime
from ex_loan_management.api.utils import get_paginated_data, api_error


class LoanApplication(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF
        from lending.loan_management.doctype.proposed_pledge.proposed_pledge import ProposedPledge

        amended_from: DF.Link | None
        applicant: DF.DynamicLink
        applicant_name: DF.Data | None
        applicant_type: DF.Literal["Loan Member"]
        co_borrower: DF.Link | None
        company: DF.Link
        description: DF.SmallText | None
        is_secured_loan: DF.Check
        is_term_loan: DF.Check
        loan_amount: DF.Currency
        loan_group: DF.Data | None
        loan_product: DF.Link
        maximum_loan_amount: DF.Currency
        nominee: DF.Link
        nominee_relation: DF.Literal["", "SPOUSE", "MOTHER", "FATHER", "SON", "DAUGHTER", "SISTER", "BROTHER", "HUSBAND", "WIFE", "FRIEND", "OTHER"]
        posting_date: DF.Date | None
        proposed_pledges: DF.Table[ProposedPledge]
        rate_of_interest: DF.Percent
        repayment_amount: DF.Currency
        repayment_method: DF.Literal["", "Repay Fixed Amount per Period", "Repay Over Number of Periods"]
        repayment_periods: DF.Int
        status: DF.Literal["Open", "Approved", "Rejected"]
        total_payable_amount: DF.Currency
        total_payable_interest: DF.Currency
    # end: auto-generated types

    def validate(self):
        self.set_pledge_amount()
        self.set_loan_amount()
        self.validate_loan_amount()

        if self.is_term_loan:
            self.validate_repayment_method()

        self.validate_loan_product()
        self.validate_employee()

        self.get_repayment_details()
        self.check_sanctioned_amount_limit()


    def on_update(self):
        if self.workflow_state == "Approved" and self.docstatus == 0:
            self.status = "Approved"
            self.submit()

			
    def on_submit(self):
        if self.co_borrower:
            # Step 1: get all approved loan applications with same co_borrower
            applications = frappe.get_all(
                "Loan Application",
                filters={
                    "co_borrower": self.co_borrower,
                    "status": "Approved"
                },
                fields=["name"]
            )
            if applications:
                application_names = [app.name for app in applications]

                # Step 2: get all Loans linked to these applications
                loans = frappe.get_all(
                    "Loan",
                    filters={
                        "loan_application": ["in", application_names],
                        "status": ["not in", ["Closed", "Written Offs"]]
                    },
                    fields=["name", "status", "applicant", "loan_application"]
                )
                if len(loans)>0:
                    frappe.throw(_("Selected co-borrower is already associated with active loan(s). Please select another co-borrower."))
               
    
    def validate_repayment_method(self):
        if self.repayment_method == "Repay Over Number of Periods" and not self.repayment_periods:
            frappe.throw(_("Please enter Repayment Periods"))

        if self.repayment_method == "Repay Fixed Amount per Period":
            if not self.repayment_amount:
                frappe.throw(_("Please enter repayment Amount"))
            if self.repayment_amount > self.loan_amount:
                frappe.throw(_("Monthly Repayment Amount cannot be greater than Loan Amount"))

    def validate_loan_product(self):
        company = frappe.get_value("Loan Product", self.loan_product, "company")
        if company != self.company:
            frappe.throw(_("Please select Loan Product for company {0}").format(frappe.bold(self.company)))

    def validate_employee(self):
        if self.applicant_type == "Employee":
            employee_company = frappe.get_value("Employee", self.applicant, "company")
            if employee_company != self.company:
                frappe.throw(
                    _("Selected employee belongs to {0}. Please select an employee from company {1}.").format(
                        frappe.bold(employee_company), frappe.bold(self.company)
                    )
                )

    def validate_loan_amount(self):
        if not self.loan_amount:
            frappe.throw(_("Loan Amount is mandatory"))

        maximum_loan_limit = frappe.db.get_value(
			"Loan Product", self.loan_product, "maximum_loan_amount"
		)
        if maximum_loan_limit and self.loan_amount > maximum_loan_limit:
            frappe.throw(
				_("Loan Amount cannot exceed Maximum Loan Amount of {0}").format(maximum_loan_limit)
			)

        if self.maximum_loan_amount and self.loan_amount > self.maximum_loan_amount:
            frappe.throw(
				_("Loan Amount exceeds maximum loan amount of {0} as per proposed securities").format(
					self.maximum_loan_amount
				)
			)

    def check_sanctioned_amount_limit(self):
        sanctioned_amount_limit = get_sanctioned_amount_limit(
			self.applicant_type, self.applicant, self.company
		)

        if sanctioned_amount_limit:
            total_loan_amount = get_total_loan_amount(self.applicant_type, self.applicant, self.company)

        if sanctioned_amount_limit and flt(self.loan_amount) + flt(total_loan_amount) > flt(
			sanctioned_amount_limit
		):
            frappe.throw(
				_("Sanctioned Amount limit crossed for {0} {1}").format(
					self.applicant_type, frappe.bold(self.applicant)
				)
			)

    def set_pledge_amount(self):
        for proposed_pledge in self.proposed_pledges:

            if not proposed_pledge.qty:
                frappe.throw(_("Qty is mandatory for loan security!"))

            if not proposed_pledge.loan_security_price:
                loan_security_price = get_loan_security_price(proposed_pledge.loan_security)

                if loan_security_price:
                    proposed_pledge.loan_security_price = loan_security_price
                else:
                    frappe.throw(
						_("No valid Loan Security Price found for {0}").format(
							frappe.bold(proposed_pledge.loan_security)
						)
					)

            proposed_pledge.amount = proposed_pledge.qty * proposed_pledge.loan_security_price
            proposed_pledge.post_haircut_amount = cint(
				proposed_pledge.amount - (proposed_pledge.amount * proposed_pledge.haircut / 100)
			)

    def get_repayment_details(self):

        if self.is_term_loan:
            if self.repayment_method == "Repay Over Number of Periods":
                self.repayment_amount = get_monthly_repayment_amount(
					self.loan_amount, self.rate_of_interest, self.repayment_periods, "Monthly"
				)

            if self.repayment_method == "Repay Fixed Amount per Period":
                monthly_interest_rate = flt(self.rate_of_interest) / (12 * 100)
                if monthly_interest_rate:
                    min_repayment_amount = self.loan_amount * monthly_interest_rate
                    if self.repayment_amount - min_repayment_amount <= 0:
                        frappe.throw(_("Repayment Amount must be greater than " + str(flt(min_repayment_amount, 2))))
                    self.repayment_periods = math.ceil(
						(math.log(self.repayment_amount) - math.log(self.repayment_amount - min_repayment_amount))
						/ (math.log(1 + monthly_interest_rate))
					)
                else:
                    self.repayment_periods = self.loan_amount / self.repayment_amount

            self.calculate_payable_amount()
        else:
            self.total_payable_amount = self.loan_amount

    def calculate_payable_amount(self):
        balance_amount = self.loan_amount
        self.total_payable_amount = 0
        self.total_payable_interest = 0

        while balance_amount > 0:
            interest_amount = rounded(balance_amount * flt(self.rate_of_interest) / (12 * 100))
            balance_amount = rounded(balance_amount + interest_amount - self.repayment_amount)

            self.total_payable_interest += interest_amount

        self.total_payable_amount = self.loan_amount + self.total_payable_interest

    def set_loan_amount(self):
        if self.is_secured_loan and not self.proposed_pledges:
            frappe.throw(_("Proposed Pledges are mandatory for secured Loans"))

        if self.is_secured_loan and self.proposed_pledges:
            self.maximum_loan_amount = 0
            for security in self.proposed_pledges:
                self.maximum_loan_amount += flt(security.post_haircut_amount)

        if not self.loan_amount and self.is_secured_loan and self.proposed_pledges:
            self.loan_amount = self.maximum_loan_amount


@frappe.whitelist()
def create_loan(source_name, target_doc=None, submit=0):
	def update_accounts(source_doc, target_doc, source_parent):
		account_details = frappe.get_all(
			"Loan Product",
			fields=[
				"payment_account",
				"loan_account",
				"interest_income_account",
				"penalty_income_account",
			],
			filters={"name": source_doc.loan_product},
		)[0]

		if source_doc.is_secured_loan:
			target_doc.maximum_loan_amount = 0

		target_doc.payment_account = account_details.payment_account
		target_doc.loan_account = account_details.loan_account
		target_doc.interest_income_account = account_details.interest_income_account
		target_doc.penalty_income_account = account_details.penalty_income_account
		target_doc.loan_application = source_name

	doclist = get_mapped_doc(
		"Loan Application",
		source_name,
		{
			"Loan Application": {
				"doctype": "Loan",
				"validation": {"docstatus": ["=", 1]},
				"postprocess": update_accounts,
			}
		},
		target_doc,
	)

	if submit:
		doclist.submit()

	return doclist


@frappe.whitelist()
def create_loan_security_assignment(loan_application, loan=None):
	loan_application_doc = frappe.get_doc("Loan Application", loan_application)

	lsa = frappe.new_doc("Loan Security Assignment")
	lsa.applicant_type = loan_application_doc.applicant_type
	lsa.applicant = loan_application_doc.applicant
	lsa.company = loan_application_doc.company
	lsa.loan_application = loan_application
	lsa.loan = loan

	for pledge in loan_application_doc.proposed_pledges:
		lsa.append(
			"securities",
			{
				"loan_security": pledge.loan_security,
				"qty": pledge.qty,
				"loan_security_price": pledge.loan_security_price,
				"haircut": pledge.haircut,
			},
		)

	lsa.save()
	lsa.submit()

	message = _("Loan Security Assignment Created : {0}").format(lsa.name)
	frappe.msgprint(message)

	return lsa.name


# This is a sandbox method to get the proposed pledges
@frappe.whitelist()
def get_proposed_pledge(securities):
	if isinstance(securities, str):
		securities = json.loads(securities)

	proposed_pledges = {"securities": []}
	maximum_loan_amount = 0

	for security in securities:
		security = frappe._dict(security)
		if not security.qty and not security.amount:
			frappe.throw(_("Qty or Amount is mandatroy for loan security"))

		security.loan_security_price = get_loan_security_price(security.loan_security)

		if not security.qty:
			security.qty = cint(security.amount / security.loan_security_price)

		security.amount = security.qty * security.loan_security_price
		security.post_haircut_amount = cint(security.amount - (security.amount * security.haircut / 100))

		maximum_loan_amount += security.post_haircut_amount

		proposed_pledges["securities"].append(security)

	proposed_pledges["maximum_loan_amount"] = maximum_loan_amount

	return proposed_pledges




def get_permission_query_conditions(user):
    print('user .....',user)
    doctype = frappe.form_dict.get("doctype")
    if not user or user == "Administrator":
        return None

    if "Agent" in frappe.get_roles(user):
        # Get employee ID linked to logged-in user
        employee_id = frappe.db.get_value("Employee", {"user_id": user}, "name")
        if not employee_id:
            return None  # No employee mapped

        # Fetch groups assigned to this employee
        groups = frappe.get_all(
            "Loan Group Assignment",
            filters={"employee": employee_id},
            pluck="loan_group"
        )
        if not groups:
            return "1=2"

        groups_str = "', '".join(groups)

        if frappe.form_dict.get("doctype") == "Collection In Hand":
			# Filter only records where employee matches logged-in user's employee
            return f"`tabCollection In Hand`.`employee` = '{employee_id}'"

        
        # For Loan Repayment Schedule → restrict indirectly via Loan → Loan Member → Group
        if frappe.form_dict.get("doctype") == "Loan Repayment Schedule":
            return f"""
                loan IN (
                    SELECT name FROM `tabLoan`
                    WHERE applicant IN (
                        SELECT name FROM `tabLoan Member`
                        WHERE `group` in ('{groups_str}')
                    )
                )
            """
        else:
            
            return f"""
                `tab{doctype}`.`applicant`  IN (
                    SELECT name FROM `tabLoan Member`
                    WHERE `group` in ('{groups_str}')
                )
            """

    return None


def has_permission(doc, user):
    if not user or user == "Administrator":
        return True

    if "Agent" in frappe.get_roles(user):
        employee_id = frappe.db.get_value("Employee", {"user_id": user}, "name")
        if not employee_id:
            return False
		
        if frappe.form_dict.get("doctype") == "Collection In Hand":
			# Filter only records where employee matches logged-in user's employee
            return doc.employee == employee_id

        groups = frappe.get_all(
            "Loan Group Assignment",
            filters={"employee": employee_id},
            pluck="loan_group"
        )
        if not groups:
            return False
            

        # Case 2: Loan Repayment Schedule → check group via linked Loan
        if doc.doctype == "Loan Repayment Schedule":
            loan_member_group = frappe.db.get_value(
                "Loan Member",
                {"name": frappe.db.get_value("Loan", doc.loan, "applicant")},
                "group"
            )
            return loan_member_group in groups
        else:
            return doc.group in groups

    return True



@frappe.whitelist()
def bulk_import_loan_applications(file_url):
    print("in import appl")
    # Get File doc
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    file_path = file_doc.get_full_path()   # full path to file in sites/private/files/ or sites/{sitename}/public/files/

    # Read file with pandas
    if file_url.endswith(".csv"):
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)
        df = df.fillna("")

    print('file_path ...', file_path, 'df ....', df)

    success = []
    errors = []

    for idx, row in df.iterrows():
        try:
            nominee_name = row.get("NOMINEE FULL NAME")
            relation = str(row.get("RELATIONSHIP OF NOMINEE WITH BORROWER")).title()
            print("relation ....",relation)
            print('row.get("ROI")...',row.get("ROI"), type(row.get("ROI")))
            # --- Get Loan Product ---
            loan_product = frappe.get_value("Loan Product", {"rate_of_interest": row.get("ROI")}, "name")
            if not loan_product:
                raise Exception(f"Loan Product '{row.get('ROI')}' not found")

            # --- Get Applicant ---
            print('row.get("MEMBER NO") ...',row.get("MEMBER NO"))
            applicant = frappe.get_value("Loan Member", {"member_id": row.get("MEMBER NO")}, "name")
            if not applicant:
                raise Exception(f"Applicant '{row.get('MEMBER NO')}' not found")
            
            nominee_details = frappe.get_value("Loan Member", {"member_name": nominee_name}, "name")
            if not nominee_details:
                raise Exception(f"Applicant '{nominee_name}' not found")

            # --- Parse Date ---
            loan_date = row.get("LOAN SANCTION DATE/ trasancation date")
            if isinstance(loan_date, str):
                for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
                    try:
                        loan_date = datetime.strptime(loan_date, fmt).date()
                        break
                    except:
                        continue

            print('loan_product ....',loan_product)

            # Prepare data
            loan_data = {
                "doctype": "Loan Application",
                "applicant_type": "Loan Member",
                "applicant": applicant,
                "company": "Excellminds (Demo)",
                "loan_product": loan_product,
                "loan_amount": row.get("LOAN AMOUNT"),
                "is_secured_loan": 0,
                "is_term_loan": 1,
                "repayment_method": "Repay Over Number of Periods",
                "repayment_periods": row.get("TERM IN MONTHS"),
                "repayment_amount": row.get("EMI"),
                "rate_of_interest": row.get("ROI"),
                "posting_date": loan_date,
				"status":"Approved",
                # "workflow_state": "Approved", 
                "nominee":nominee_details,
                "nominee_relation":relation
            }

            loan_doc = frappe.get_doc(loan_data)
            loan_doc.insert()
            loan_doc.db_set("workflow_state", "Approved", update_modified=False)
            loan_doc.submit()

            success.append(loan_doc.name)

        except Exception as e:
            print('e .....',e)
            frappe.log_error(f"Bulk Loan Import Error (Row {idx+1}): {str(e)}")
            errors.append(f"Row {idx+1}: {str(e)}")

    return f"success_count: {len(success)}, error_count: {len(errors)}"














import frappe

@frappe.whitelist()
def create_loan_application():
    try:
        data = frappe.form_dict  # works for JSON body and form-data
        user_doc = frappe.get_doc("User", frappe.session.user)
        print('user_doc ......',user_doc)
        try:
            emp_details = frappe.get_doc("Employee", {"user_id": user_doc.name})
            company = emp_details.company
        except:
            company = ""

        # Step 1: Prepare Loan Application doc
        doc = frappe.get_doc({
            "doctype": "Loan Application",
            "applicant_type": data.get("applicant_type", "Loan Member"),
            "applicant": data.get("applicant"),
            "applicant_name": data.get("applicant_name"),
            "co_borrower": data.get("co_borrower"),
            "nominee":data.get("nominee"),
            "nominee_relation":data.get("nominee_relation"),
            "company": company,
            "loan_product": data.get("loan_product"),
            "loan_amount": data.get("loan_amount"),
            "is_term_loan": data.get("is_term_loan") or 0,
            "rate_of_interest": data.get("rate_of_interest"),
            "description": data.get("description"),
            "repayment_method": "Repay Over Number of Periods",
            "repayment_periods": data.get("repayment_periods"),
            "status": "Open",
        })

        
        # Step 3: Insert Loan Application (runs validate() automatically)
        doc.insert(ignore_permissions=True)
        new_doc = apply_workflow(doc, "Submit for verification")
        frappe.db.commit()

        return {
            "status": "success",
            "status_code": 201,
            "msg": "Loan Application Created Successfully"
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Loan Application API Error")
        # return {"status": "error", "message": str(e)}
        return api_error(e)




from frappe.model.workflow import apply_workflow

@frappe.whitelist()
def send_for_verification(application_name):
    """
    Trigger workflow action 'Submit for verification'
    """
    try:
        # Load the Loan Application
        doc = frappe.get_doc("Loan Application", application_name)

        # Apply workflow transition
        new_doc = apply_workflow(doc, "Submit for verification")

        frappe.db.commit()

        return {
            "status": "success",
            "status_code": 201,
            "msg": f"Loan Application {application_name} submitted for verification",
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Send for Verification API Error")
        # return {"status": "error", "message": str(e)}
        return api_error(e)







update_fields = [
    "name",
    "applicant_type",
	"applicant",
	"applicant_name",
	"co_borrower",
	"company",
	"posting_date",
	"status",
	"loan_product",
	"is_term_loan",
	"loan_amount",
	"rate_of_interest",
	"description",
	"maximum_loan_amount",
	"repayment_method",
	"total_payable_amount",
	"repayment_periods",
	"repayment_amount",
	"total_payable_interest",
	"amended_from",
    "workflow_state",
    "nominee",
    "nominee_relation",
    "is_secured_loan",
    "loan_group",
]

"""
	Get Loan Application List (with optional pagination, search & sorting)
"""
@frappe.whitelist()
def loan_application_list(page=1, page_size=10, search=None, sort_by="name", sort_order="asc", is_pagination=False, loan_group=None, **kwargs):
    is_pagination = frappe.utils.sbool(is_pagination)  # convert "true"/"false"/1/0 into bool
    extra_params = {"search": search} if search else {}
    if "cmd" in kwargs:
        del kwargs["cmd"]

    # 🔹 Collect filters from kwargs (all query params except the defaults)
    filters = {}
    for k, v in kwargs.items():
        if v not in [None, ""]:   # skip empty params
            filters[k] = v

    # 🔹 Handle loan_group filter (from Loan Member)
    user = frappe.session.user

    if "Agent" in frappe.get_roles(user):
        employee_id = frappe.db.get_value("Employee", {"user_id": user}, "name")
        if not employee_id:
            return None  # No employee mapped

        # Fetch loan groups assigned to this employee
        groups = frappe.get_all(
            "Loan Group Assignment",
            filters={"employee": employee_id},
            pluck="loan_group"
        )

        if not groups:
            return {
                "count": 0,
                "next": None,
                "previous": None,
                "results": []
            }

        # 🔹 If agent selects a group → filter only that group
        if loan_group:
            groups = [loan_group] if loan_group in groups else []

        # If no valid group remains → no records
        if not groups:
            return {
                "count": 0,
                "next": None,
                "previous": None,
                "results": []
            }

        # Fetch Loan Members belonging to allowed groups
        member_ids = frappe.get_all(
            "Loan Member",
            filters={"group": ["in", groups]},
            pluck="name"
        )

        if member_ids:
            filters["applicant"] = ["in", member_ids]
        else:
            return {
                "count": 0,
                "next": None,
                "previous": None,
                "results": []
            }
    base_url = frappe.request.host_url.rstrip("/") + frappe.request.path

    return get_paginated_data(
        doctype="Loan Application",
        fields=update_fields,
        filters=filters,   # ✅ Now includes applicant filter if loan_group provided
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        page=int(page),
        page_size=int(page_size),
        search_fields=["applicant_name","applicant","name"],
        is_pagination=is_pagination,
        base_url=base_url,
        extra_params=extra_params,
        link_fields={"nominee": "member_name","co_borrower": "member_name","applicant": "member_id"},  # 👈 you can also expand Loan Member fields if needed
        link_images_fields={"applicant": "member_image"},  # 👈 you can also expand Loan Member fields if needed
        dynamic_search_fields = {"applicant":{"doctype": "Loan Member", "field": "member_id"},}
    )





@frappe.whitelist()
def get_loan_members_for_user(doctype, txt, searchfield, start, page_len, filters):
    user = frappe.session.user

    # Always prefer searching by member_name
    search_field = "member_name"
    print(f"Search on field: {search_field}, txt: {txt}")

    if user == "Administrator":
        return frappe.db.sql(f"""
            SELECT name, member_name
            FROM `tabLoan Member`
            WHERE `{search_field}` LIKE %s
            ORDER BY member_name ASC
            LIMIT %s OFFSET %s
        """, (f"%{txt}%", page_len, start))

    if "Agent" in frappe.get_roles(user):
        employee_id = frappe.db.get_value("Employee", {"user_id": user}, "name")
        if not employee_id:
            return []

        groups = frappe.get_all(
            "Loan Group Assignment",
            filters={"employee": employee_id},
            pluck="loan_group"
        )
        if not groups:
            return []

        # Use safe placeholders instead of string join
        placeholders = ", ".join(["%s"] * len(groups))
        query = f"""
            SELECT name, member_name
            FROM `tabLoan Member`
            WHERE `group` IN ({placeholders})
            AND `{search_field}` LIKE %s
            ORDER BY member_name ASC
            LIMIT %s OFFSET %s
        """
        return frappe.db.sql(query, (*groups, f"%{txt}%", page_len, start))

    # Default for other users
    return frappe.db.sql(f"""
        SELECT name, member_name
        FROM `tabLoan Member`
        WHERE `{search_field}` LIKE %s
        ORDER BY member_name ASC
        LIMIT %s OFFSET %s
    """, (f"%{txt}%", page_len, start))



@frappe.whitelist()
def loan_application_get(name):
    """
    Get Loan Application by name (primary key)
    Returns linked Loan Group name and full URLs for image fields
    """
    if not name:
        frappe.throw("Loan Application name is required")

    # Fetch Loan Member
    applications = frappe.get_all(
        "Loan Application",
        filters={"name": name},
        fields=update_fields
    )

    if not applications:
        return {}

    application = applications[0]

    return application

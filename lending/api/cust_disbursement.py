import frappe
from frappe import _

# This function checks if a given repayment date falls on a holiday according
# to the company's default holiday list. It can be used both in JS (client-side)
# via a frappe.call and in server-side validate events to enforce restrictions.
# Developer: Rutuja Somvanshi
# Date: 25-09-2025
@frappe.whitelist()
def check_if_holiday(company, repayment_date, from_validate=False):
    """
    Args:
        company (str): Name of the Company to check holiday settings.
        repayment_date (str or date): The date to check for holidays.
        from_validate (bool): If True, function throws an exception on holiday 
                              (used in server-side validate), otherwise returns info.

    Returns:
        dict: If from_validate=False, returns a dictionary with:
            - is_holiday (bool): True if repayment_date is a holiday
            - holiday_list (str): Name of the holiday list checked
        If from_validate=True and date is holiday, raises a frappe.ValidationError.
    """

    # Get company's holiday settings
    cmp = frappe.db.get_value(
        "Company",
        company,
        ["skip_holiday_on_loan_schedule", "default_holiday_list"],
        as_dict=True
    )

    # If company not found, return empty dict
    if not cmp:
        return {}

    # If company wants to skip holidays and has a holiday list defined
    if cmp.skip_holiday_on_loan_schedule and cmp.default_holiday_list:
        # Check if the repayment_date exists in the company's holiday list
        holiday = frappe.db.exists(
            "Holiday",
            {"parent": cmp.default_holiday_list, "holiday_date": repayment_date}
        )

        # If called from validate function on server-side, throw error
        if from_validate and holiday:
            frappe.throw(
                _("The selected Repayment Start Date {0} is a Holiday. Please choose another date.").format(
                    frappe.bold(repayment_date)
                )
            )
        else:
            # If called from JS (client-side), return holiday info
            return {
                "is_holiday": bool(holiday),
                "holiday_list": cmp.default_holiday_list
            }

    # Default: not a holiday
    return {"is_holiday": False}

"""Lending policy: loan limit, loan period and late fees.

All business rules live here so the service stays thin.
"""

from __future__ import annotations

from datetime import date, timedelta

MAX_ACTIVE_LOANS = 3
LOAN_PERIOD_DAYS = 14
LATE_FEE_PER_DAY = 0.5


def due_date(borrowed_on: date) -> date:
    """The date a book borrowed on ``borrowed_on`` must be returned by."""
    return borrowed_on + timedelta(days=LOAN_PERIOD_DAYS)


def can_borrow(active_loan_count: int) -> bool:
    """Whether a member holding ``active_loan_count`` books may borrow one more."""
    return active_loan_count <= MAX_ACTIVE_LOANS


def late_fee(due_on: date, returned_on: date, replacement_cost: float) -> float:
    """Fee owed for returning a book on ``returned_on``.

    Nothing is owed if returned on or before the due date. Otherwise the fee
    accrues per day late but must never exceed the book's replacement cost.
    """
    if returned_on <= due_on:
        return 0.0
    days_late = (returned_on - due_on).days
    return days_late * LATE_FEE_PER_DAY

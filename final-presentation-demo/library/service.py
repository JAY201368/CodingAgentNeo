"""LendingService: borrow, return and overdue reporting."""

from __future__ import annotations

from datetime import date

from library import rules
from library.models import Loan
from library.repository import LibraryRepository


class BorrowError(Exception):
    """Raised when a borrow request violates a lending rule."""


class LendingService:
    def __init__(self, repo: LibraryRepository) -> None:
        self.repo = repo

    def borrow(self, member_id: str, isbn: str, on: date) -> Loan:
        self.repo.get_member(member_id)
        book = self.repo.get_book(isbn)

        active = self.repo.active_loans_for(member_id)
        if not rules.can_borrow(len(active)):
            raise BorrowError("member has reached the active loan limit")
        if book.copies_available <= 0:
            raise BorrowError(f"no copies available for {isbn}")

        book.copies_available -= 1
        loan = Loan(
            loan_id=self.repo.next_loan_id(),
            isbn=isbn,
            member_id=member_id,
            borrowed_on=on,
            due_on=rules.due_date(on),
        )
        self.repo.add_loan(loan)
        return loan

    def return_book(self, loan_id: str, on: date) -> float:
        loan = self.repo.get_loan(loan_id)
        if not loan.is_active:
            raise BorrowError(f"loan already returned: {loan_id}")

        loan.returned_on = on
        book = self.repo.get_book(loan.isbn)
        return rules.late_fee(loan.due_on, on, book.replacement_cost)

    def overdue_loans(self, as_of: date) -> list[Loan]:
        return [
            loan
            for loan in self.repo.all_loans()
            if loan.is_active and loan.due_on < as_of
        ]

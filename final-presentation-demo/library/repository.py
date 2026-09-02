"""In-memory storage for books, members and loans."""

from __future__ import annotations

from library.models import Book, Loan, Member


class NotFoundError(KeyError):
    """Raised when a requested entity does not exist."""


class LibraryRepository:
    def __init__(self) -> None:
        self._books: dict[str, Book] = {}
        self._members: dict[str, Member] = {}
        self._loans: dict[str, Loan] = {}
        self._loan_seq = 0

    # --- registration -------------------------------------------------
    def add_book(self, book: Book) -> None:
        self._books[book.isbn] = book

    def add_member(self, member: Member) -> None:
        self._members[member.member_id] = member

    def add_loan(self, loan: Loan) -> None:
        self._loans[loan.loan_id] = loan

    # --- lookups ------------------------------------------------------
    def get_book(self, isbn: str) -> Book:
        if isbn not in self._books:
            raise NotFoundError(f"unknown book: {isbn}")
        return self._books[isbn]

    def get_member(self, member_id: str) -> Member:
        if member_id not in self._members:
            raise NotFoundError(f"unknown member: {member_id}")
        return self._members[member_id]

    def get_loan(self, loan_id: str) -> Loan:
        if loan_id not in self._loans:
            raise NotFoundError(f"unknown loan: {loan_id}")
        return self._loans[loan_id]

    def all_loans(self) -> list[Loan]:
        return list(self._loans.values())

    def active_loans_for(self, member_id: str) -> list[Loan]:
        return [
            loan
            for loan in self._loans.values()
            if loan.member_id == member_id and loan.is_active
        ]

    def next_loan_id(self) -> str:
        self._loan_seq += 1
        return f"loan-{self._loan_seq}"

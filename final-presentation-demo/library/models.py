"""Domain records for the lending system."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Book:
    isbn: str
    title: str
    copies_total: int
    copies_available: int
    replacement_cost: float


@dataclass
class Member:
    member_id: str
    name: str


@dataclass
class Loan:
    loan_id: str
    isbn: str
    member_id: str
    borrowed_on: date
    due_on: date
    returned_on: date | None = None

    @property
    def is_active(self) -> bool:
        return self.returned_on is None

"""Behavioural tests for the lending system.

These describe the intended business rules. Run them with
``python3 run_tests.py`` from the project root.
"""

import unittest
from datetime import date

from library.models import Book, Member
from library.repository import LibraryRepository
from library.service import BorrowError, LendingService


def make_service() -> tuple[LibraryRepository, LendingService]:
    repo = LibraryRepository()
    repo.add_member(Member("m1", "Alice"))
    repo.add_book(Book("isbn-1", "Clean Code", 2, 2, 30.0))
    repo.add_book(Book("isbn-2", "SICP", 5, 5, 40.0))
    repo.add_book(Book("isbn-3", "TAPL", 1, 1, 60.0))
    repo.add_book(Book("isbn-4", "TAOCP", 1, 1, 100.0))
    return repo, LendingService(repo)


class BorrowTests(unittest.TestCase):
    def test_borrow_decrements_availability(self):
        repo, svc = make_service()
        svc.borrow("m1", "isbn-1", date(2026, 1, 1))
        self.assertEqual(repo.get_book("isbn-1").copies_available, 1)

    def test_due_date_is_two_weeks(self):
        _, svc = make_service()
        loan = svc.borrow("m1", "isbn-1", date(2026, 1, 1))
        self.assertEqual(loan.due_on, date(2026, 1, 15))

    def test_cannot_exceed_active_loan_limit(self):
        _, svc = make_service()
        svc.borrow("m1", "isbn-1", date(2026, 1, 1))
        svc.borrow("m1", "isbn-2", date(2026, 1, 1))
        svc.borrow("m1", "isbn-3", date(2026, 1, 1))
        with self.assertRaises(BorrowError):
            svc.borrow("m1", "isbn-4", date(2026, 1, 1))


class ReturnTests(unittest.TestCase):
    def test_return_restores_availability(self):
        repo, svc = make_service()
        loan = svc.borrow("m1", "isbn-1", date(2026, 1, 1))
        self.assertEqual(repo.get_book("isbn-1").copies_available, 1)
        svc.return_book(loan.loan_id, date(2026, 1, 10))
        self.assertEqual(repo.get_book("isbn-1").copies_available, 2)

    def test_on_time_return_has_no_fee(self):
        _, svc = make_service()
        loan = svc.borrow("m1", "isbn-1", date(2026, 1, 1))
        fee = svc.return_book(loan.loan_id, date(2026, 1, 15))
        self.assertEqual(fee, 0.0)

    def test_late_fee_accrues_per_day(self):
        _, svc = make_service()
        loan = svc.borrow("m1", "isbn-1", date(2026, 1, 1))  # due 2026-01-15
        fee = svc.return_book(loan.loan_id, date(2026, 1, 20))  # 5 days late
        self.assertAlmostEqual(fee, 2.5)

    def test_late_fee_capped_at_replacement_cost(self):
        _, svc = make_service()
        loan = svc.borrow("m1", "isbn-1", date(2026, 1, 1))  # cost 30.0
        fee = svc.return_book(loan.loan_id, date(2026, 6, 1))  # far past due
        self.assertEqual(fee, 30.0)


class OverdueTests(unittest.TestCase):
    def test_overdue_lists_unreturned_past_due(self):
        _, svc = make_service()
        loan = svc.borrow("m1", "isbn-1", date(2026, 1, 1))  # due 2026-01-15
        overdue = svc.overdue_loans(date(2026, 1, 20))
        self.assertEqual([item.loan_id for item in overdue], [loan.loan_id])

    def test_not_overdue_before_due_date(self):
        _, svc = make_service()
        svc.borrow("m1", "isbn-1", date(2026, 1, 1))
        self.assertEqual(svc.overdue_loans(date(2026, 1, 10)), [])


if __name__ == "__main__":
    unittest.main()

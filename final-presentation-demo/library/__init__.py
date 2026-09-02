"""A small in-memory library lending system used for the demo task.

Modules:
    models      immutable-ish domain records (Book, Member, Loan)
    repository  in-memory storage and lookups
    rules       lending policy: loan limit, loan period, late fees
    service     LendingService orchestrating borrow / return / overdue
"""

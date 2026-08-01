"""Errors exposed by goal lifecycle workflows."""


class GoalLifecycleError(RuntimeError):
    """Report a failed closed lifecycle precondition or operation."""

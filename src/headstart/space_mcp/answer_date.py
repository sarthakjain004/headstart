"""The day an answer counts to: a posting's age, and whether a stated end day has passed."""

from __future__ import annotations

from datetime import UTC, date, datetime


def today() -> date:
    """Today in UTC; its own function so a test or a replay pins it for every tool at once."""
    return datetime.now(UTC).date()

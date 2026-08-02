"""Tests for the Résumé→Query derivation (headstart.resume_query, ADR-0032).

The contract under test is the search design's: a Query names a role and nothing else. The
scrub is the load-bearing piece — the prompt *asks* the model to omit years and salary, but
only the code can *guarantee* it, and a miss would hide structured constraints inside the
embedding where no Search filter reveals them. ``ask`` is stubbed throughout; no LLM, no
tunnel, runs in CI.
"""

from __future__ import annotations

import pytest

from headstart import resume_query as rq

_RESUME = "Jane Doe. Senior backend engineer at Acme. Python, Go, Kafka. B.Tech 2018."


def test_happy_path_passes_the_query_through():
    assert (
        rq.query_for(
            _RESUME, ask=lambda p: "backend engineer, distributed systems, Python/Go"
        )
        == "backend engineer, distributed systems, Python/Go"
    )


def test_resume_text_reaches_the_prompt_and_nothing_else_does():
    seen = {}

    def ask(prompt: str) -> str:
        seen["prompt"] = prompt
        return "backend engineer"

    rq.query_for(_RESUME, ask=ask)
    assert _RESUME in seen["prompt"]


def test_empty_resume_is_rejected_before_spending_an_llm_call():
    def ask(prompt: str) -> str:  # pragma: no cover — must never run
        raise AssertionError("ask() called for an empty résumé")

    with pytest.raises(rq.EmptyResume):
        rq.query_for("   \n  ", ask=ask)


def test_oversized_paste_is_rejected_before_spending_an_llm_call():
    with pytest.raises(rq.ResumeTooLong):
        rq.query_for("x" * (rq.MAX_RESUME_CHARS + 1), ask=lambda p: "never")


def test_cleanup_unwraps_quotes_fences_and_preamble():
    reply = '```\n"Backend Engineer, fintech, Python."\n```'
    assert (
        rq.query_for(_RESUME, ask=lambda p: reply)
        == "Backend Engineer, fintech, Python"
    )


def test_cleanup_skips_a_fence_language_tag():
    """Regression: stripping backticks before checking for fences turned ```python into the
    "query" `python`."""
    reply = "```python\nbackend engineer, Django\n```"
    assert rq.query_for(_RESUME, ask=lambda p: reply) == "backend engineer, Django"


def test_cleanup_takes_the_first_real_line_of_a_rambling_reply():
    reply = "backend engineer, Python\n\nExplanation: I chose this because…"
    assert rq.query_for(_RESUME, ask=lambda p: reply) == "backend engineer, Python"


def test_scrub_removes_years_of_experience():
    reply = "backend engineer, 7+ years of experience, Python, Kafka"
    assert (
        rq.query_for(_RESUME, ask=lambda p: reply) == "backend engineer, Python, Kafka"
    )


def test_scrub_removes_salary_phrasing():
    reply = "backend engineer, ₹30 LPA, fintech, salary negotiable"
    out = rq.query_for(_RESUME, ask=lambda p: reply)
    assert "lpa" not in out.lower() and "salary" not in out.lower()
    assert "backend engineer" in out and "fintech" in out


def test_reply_that_scrubs_to_nothing_raises_empty_query():
    with pytest.raises(rq.EmptyQuery):
        rq.query_for(_RESUME, ask=lambda p: "10+ years, $200k salary")


def test_router_errors_pass_through_untouched():
    class Boom(RuntimeError):
        pass

    def ask(prompt: str) -> str:
        raise Boom("router down")

    with pytest.raises(
        Boom
    ):  # the route maps RouterUnavailable → 503; not this module's job
        rq.query_for(_RESUME, ask=ask)

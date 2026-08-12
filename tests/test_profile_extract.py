"""Tests for the Résumé→Profile extraction (headstart.profile_extract, ADR-0041).

The contract under test is the search design's: the ``query`` sentence names a role and
nothing else. The scrub is the load-bearing piece — the prompt *asks* the model to omit
years and salary, but only the code can *guarantee* it, and a miss would hide structured
constraints inside the embedding where no Search filter reveals them. The facts are where
those constraints belong, each typed and bounded. ``ask`` is stubbed throughout; no LLM,
no tunnel, runs in CI.
"""

from __future__ import annotations

import json

import pytest

from headstart import profile_extract as pe

_RESUME = "Jane Doe. Senior backend engineer at Acme. Python, Go, Kafka. B.Tech 2018."

_REPLY = {
    "query": "backend engineer, distributed systems, Python/Go",
    "title": "Senior Backend Engineer",
    "years": 7,
    "skills": ["Python", "Go", "Kafka"],
    "roles": ["Senior Backend Engineer at Acme", "SDE II at Beta"],
    "education": "B.Tech Computer Science",
    "location": "Bengaluru, India",
}


def _reply(**over):
    data = {**_REPLY, **over}
    return lambda prompt: json.dumps(data)


def test_happy_path_extracts_sentence_and_facts():
    out = pe.extract(_RESUME, ask=_reply())
    assert out["query"] == "backend engineer, distributed systems, Python/Go"
    assert out["title"] == "Senior Backend Engineer"
    assert out["years"] == 7
    assert out["skills"] == "Python, Go, Kafka"  # lists join into one editable line
    assert out["roles"] == "Senior Backend Engineer at Acme, SDE II at Beta"
    assert out["location"] == "Bengaluru, India"


def test_resume_text_reaches_the_prompt_and_nothing_else_does():
    seen = {}

    def ask(prompt: str) -> str:
        seen["prompt"] = prompt
        return json.dumps(_REPLY)

    pe.extract(_RESUME, ask=ask)
    assert _RESUME in seen["prompt"]


def test_empty_resume_is_rejected_before_spending_an_llm_call():
    def ask(prompt: str) -> str:  # pragma: no cover — must never run
        raise AssertionError("ask() called for an empty résumé")

    with pytest.raises(pe.EmptyResume):
        pe.extract("   \n  ", ask=ask)


def test_oversized_paste_is_rejected_before_spending_an_llm_call():
    with pytest.raises(pe.ResumeTooLong):
        pe.extract("x" * (pe.MAX_RESUME_CHARS + 1), ask=lambda p: "never")


def test_reply_json_survives_fences_and_preamble():
    reply = "Here is the profile:\n```json\n" + json.dumps(_REPLY) + "\n```"
    assert pe.extract(_RESUME, ask=lambda p: reply)["years"] == 7


def test_reply_that_is_not_json_raises_empty_extraction():
    with pytest.raises(pe.EmptyExtraction):
        pe.extract(_RESUME, ask=lambda p: "I cannot help with that.")


def test_scrub_removes_years_and_salary_from_the_query_only():
    out = pe.extract(
        _RESUME,
        ask=_reply(query="backend engineer, 7+ years of experience, ₹30 LPA, Kafka"),
    )
    assert out["query"] == "backend engineer, Kafka"
    assert out["years"] == 7  # the fact keeps what the sentence must not


def test_query_that_scrubs_to_nothing_raises_empty_extraction():
    with pytest.raises(pe.EmptyExtraction):
        pe.extract(_RESUME, ask=_reply(query="10+ years, $200k salary"))


def test_bad_years_become_none_rather_than_garbage():
    assert pe.extract(_RESUME, ask=_reply(years="a decade"))["years"] is None
    assert pe.extract(_RESUME, ask=_reply(years=-3))["years"] is None
    assert pe.extract(_RESUME, ask=_reply(years=250))["years"] is None
    assert pe.extract(_RESUME, ask=_reply(years=None))["years"] is None


def test_missing_fact_keys_become_empty_strings():
    out = pe.extract(_RESUME, ask=_reply(education=None, location=None))
    assert out["education"] == "" and out["location"] == ""


def test_facts_are_length_bounded():
    out = pe.extract(_RESUME, ask=_reply(title="x" * 500))
    assert len(out["title"]) == 200


def test_router_errors_pass_through_untouched():
    class Boom(RuntimeError):
        pass

    def ask(prompt: str) -> str:
        raise Boom("router down")

    with pytest.raises(
        Boom
    ):  # the route maps RouterUnavailable → 503; not this module's job
        pe.extract(_RESUME, ask=ask)

"""Which tech skills a description mentions — `headstart.serving.tech_skills` (ADR-0324).

Two halves: the shipped vocabulary against the terms that make skill matching hard (a language
named like an English word, a symbol in a name, one name inside another), and the matching rules
themselves against a small vocabulary built here, so each rule is read on its own.
"""

from __future__ import annotations

import json
import pathlib
import tomllib

import pytest

from headstart.serving import tech_skills


@pytest.fixture(scope="module")
def shipped() -> tech_skills.Vocabulary:
    return tech_skills.vocabulary()


def _found(vocabulary: tech_skills.Vocabulary, text: str, company=None) -> set[str]:
    return vocabulary.mentioned(text, company)


# ---- the shipped vocabulary ----


def test_a_wheel_carries_the_vocabulary_where_the_module_looks_first():
    """A wheel has no `config/`: pyproject force-includes the file beside the module, the first
    place `located` looks (ADR-0274's arrangement for the role families)."""
    pyproject = pathlib.Path(__file__).resolve().parents[1] / "pyproject.toml"
    included = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["hatch"][
        "build"
    ]["targets"]["wheel"]["force-include"]
    first = tech_skills._candidates()[0]
    assert included["config/tech_skills.json"] == "headstart/serving/tech_skills.json"
    assert first.parts[-3:] == ("headstart", "serving", "tech_skills.json")


def test_the_shipped_vocabulary_loads_from_config_and_names_each_skill_once(shipped):
    document = json.loads(tech_skills.located().read_text(encoding="utf-8"))
    assert len(shipped.skills) == len(document["skills"]) >= 300
    assert len({skill.name for skill in shipped.skills}) == len(shipped.skills)
    assert {skill.kind for skill in shipped.skills} <= set(shipped.kinds)


@pytest.mark.parametrize(
    ("text", "named", "not_named"),
    [
        ("Strong C++ and C# skills", {"C++", "C#"}, {"C"}),
        ("Experience with C/C++ on embedded targets", {"C", "C++"}, set()),
        ("Series C funded, backed by great investors", set(), {"C"}),
        ("Build services in .NET Core and ASP.NET", {".NET"}, set()),
        ("Email us at jobs@example.net today", set(), {".NET"}),
        ("Node.js, Express and MongoDB", {"Node.js", "Express", "MongoDB"}, set()),
        ("Python, Go or Rust", {"Python", "Go", "Rust"}, set()),
        ("Go-to-market with Python and Java", {"Python", "Java"}, {"Go"}),
        ("We go the extra mile", set(), {"Go"}),
        ("Python or R for statistics", {"Python", "R"}, set()),
        ("R&D teams, Python preferred", {"Python"}, {"R"}),
        ("JavaScript and TypeScript", {"JavaScript", "TypeScript"}, {"Java"}),
        ("Java 17 and Spring Boot", {"Java", "Spring"}, {"JavaScript"}),
        ("Offices in West Java, Indonesia", set(), {"Java"}),
        ("Zero Trust architecture and trust boundaries", {"Zero trust"}, {"Rust"}),
        ("Apache Spark and PySpark pipelines", {"Spark"}, set()),
        ("Backed by Spark Capital and others", set(), {"Spark"}),
        ("We spark joy with Python", {"Python"}, {"Spark"}),
        ("HTML, CSS and XML feeds", {"HTML", "CSS"}, {"Machine learning"}),
        ("AI/ML platforms", {"Machine learning"}, set()),
        ("React Native apps", {"React Native"}, {"React"}),
        ("React and Redux", {"React", "Redux"}, set()),
        ("SQL Server and T-SQL", {"SQL Server", "SQL"}, set()),
        ("PCI-Express devices and PCI DSS audits", {"PCI DSS"}, {"Express"}),
        ("SAS/SATA drives and NVMe", set(), {"SAS"}),
        ("Python, R or SAS preferred", {"SAS", "R", "Python"}, set()),
        ("Configure Cisco IOS-XE routers", {"Cisco"}, {"iOS"}),
        ("Native iOS apps in Swift", {"iOS", "Swift"}, set()),
        ("Databricks (Unity Catalog)", {"Databricks"}, {"Unity"}),
        ("Game engines like Unity and Unreal", {"Unity", "Unreal Engine"}, set()),
        ("Deploy with ARM templates and Bicep", {"Infrastructure as code"}, {"ARM"}),
        ("Firmware on ARM Cortex-M", {"ARM", "Firmware"}, set()),
        ("Excel in a fast-paced team", set(), {"Excel"}),
        ("Advanced Excel and Power BI", {"Excel", "Power BI"}, set()),
        ("Mobile device management (MDM) with Intune", {"Intune"}, set()),
    ],
)
def test_the_shipped_vocabulary_reads_the_hard_terms(shipped, text, named, not_named):
    found = _found(shipped, text)
    assert named <= found, named - found
    assert not (not_named & found), not_named & found


def test_a_title_case_description_counts_a_capitalised_word_only_in_a_list(shipped):
    title_case = " ".join(
        ["Ensuring Swift And Efficient Problem Resolution For Every Customer Ticket"]
        * 4
    )
    assert "Swift" not in _found(shipped, title_case)
    listed = title_case + " Using Kotlin, Swift And Java."
    assert "Swift" in _found(shipped, listed)


def test_the_employer_is_not_counted_as_its_own_skill(shipped):
    text = "Salesforce is the #1 AI CRM. You will build Python services."
    assert "Salesforce" in _found(shipped, text)
    assert _found(shipped, text, company="Salesforce") == {"Python"}


# ---- the rules, on a vocabulary of our own ----


def _vocabulary(*skills: dict) -> tech_skills.Vocabulary:
    return tech_skills.Vocabulary(
        {"kinds": {"language": "Languages", "cloud": "Cloud"}, "skills": list(skills)}
    )


def test_a_term_matches_whole_tokens_case_blind_unless_cased():
    vocabulary = _vocabulary(
        {"name": "Kotlin", "kind": "language", "terms": ["Kotlin"]},
        {
            "name": "Rust",
            "kind": "language",
            "terms": [{"text": "Rust", "cased": True}],
        },
    )
    assert _found(vocabulary, "we use KOTLIN and Rust") == {"Kotlin", "Rust"}
    assert _found(vocabulary, "kotlinx and rust-proofing and Trust") == set()


def test_a_listed_term_counts_only_beside_a_counted_skill_of_the_kind_it_names():
    vocabulary = _vocabulary(
        {"name": "Python", "kind": "language", "terms": ["Python"]},
        {"name": "AWS", "kind": "cloud", "terms": ["AWS"]},
        {
            "name": "R",
            "kind": "language",
            "terms": [{"text": "R", "cased": True, "listed": ["language"]}],
        },
    )
    assert _found(vocabulary, "Python and R") == {"Python", "R"}
    assert _found(vocabulary, "R, Python") == {"Python", "R"}
    assert _found(vocabulary, "AWS or R") == {"AWS"}
    assert _found(vocabulary, "Python. R is next") == {"Python"}


def test_listed_terms_chain_from_one_counted_skill():
    vocabulary = _vocabulary(
        {"name": "C++", "kind": "language", "terms": ["C++"]},
        {
            "name": "C",
            "kind": "language",
            "terms": [{"text": "C", "cased": True, "listed": True}],
        },
        {
            "name": "Go",
            "kind": "language",
            "terms": [{"text": "Go", "cased": True, "listed": True}],
        },
    )
    assert _found(vocabulary, "Go, C and C++") == {"Go", "C", "C++"}


def test_not_before_and_not_after_drop_a_match_by_its_neighbours():
    vocabulary = _vocabulary(
        {
            "name": "Java",
            "kind": "language",
            "terms": [
                {"text": "Java", "not_after": ["west"], "not_before": [", Indonesia"]}
            ],
        }
    )
    assert _found(vocabulary, "West Java") == set()
    assert _found(vocabulary, "Java, Indonesia") == set()
    assert _found(vocabulary, "Java, Kotlin") == {"Java"}
    assert _found(vocabulary, "Midwest Java shop") == {
        "Java"
    }  # "west" as a whole word only


def test_the_longest_term_at_a_place_wins():
    vocabulary = _vocabulary(
        {"name": "Azure", "kind": "cloud", "terms": ["Azure"]},
        {
            "name": "Azure Data Factory",
            "kind": "cloud",
            "terms": ["Azure Data Factory"],
        },
    )
    assert _found(vocabulary, "Azure Data Factory pipelines") == {"Azure Data Factory"}
    assert _found(vocabulary, "Azure, Data Factory") == {"Azure"}


def test_no_text_mentions_nothing():
    vocabulary = _vocabulary(
        {"name": "Python", "kind": "language", "terms": ["Python"]}
    )
    assert vocabulary.mentioned(None) == set() and vocabulary.mentioned("") == set()


def test_a_skill_of_an_unknown_kind_or_named_twice_is_refused():
    with pytest.raises(ValueError, match="unknown kind"):
        _vocabulary({"name": "X", "kind": "nope", "terms": ["X"]})
    with pytest.raises(ValueError, match="twice"):
        _vocabulary(
            {"name": "X", "kind": "cloud", "terms": ["X"]},
            {"name": "X", "kind": "cloud", "terms": ["Y"]},
        )

"""Who operates a Board — the employer itself, an IT services firm, a staffing firm, or an
aggregator.

The Hot tab ranks companies by hiring activity, and the loudest on every lens are not all
employers: `lever:jobgether` re-posts other companies' jobs, and SmartRecruiters' staffing
agencies post the same client contract in twenty cities. Serving them as "the companies hiring
hardest right now" is wrong in a way a user notices immediately, so `company_directory` labels
every company with what this module assigns its Boards, each ranked row carries that label, and
the tab hides staffing firms and job boards by default. An IT services firm (Wipro, Infosys,
TCS) employs the engineers it posts for, so it stays on the tab, labelled (the owner's call,
critic round 17, ADR-0238).

**This is a curated list, not a classifier, and that is a measured decision.** A stratified
sample of 114 Boards (of the 2,916 with >=25 open roles) was hand-labelled on 2026-09-21 and
every cheap feature available was scored against it:

    churn (7d new / open) >= 0.5   precision 60.9%   recall  9.9%
    title repetition >= 2.0        precision 26.3%   recall 11.4%
    company-name vocabulary        precision 52.0%   recall 46.0%
    name OR churn >= 0.5           precision 52.2%   recall 53.3%   <- best, F1 52.7

At 52% precision half of everything demoted would be a real employer: the name-vocabulary rule
flags Cerebras Systems, Skylo Technologies and Julius Baer for containing "Systems",
"Technologies" and "Solutions". IT services firms hire at ordinary rates and have ordinary
names, so no surface feature separates them — Capgemini churns at 0.25, CI&T at 0.29,
Foundever at 0.20, and Jobs for Lebanon, an actual job board, at 0.04. The thing that
separates them is what the company *does*, which none of those features observes.

So the scope is deliberately the head of one ranked list rather than the whole index. Measured
2026-09-26 against the Expansion lens over its 7-day window (Sep 19 → Sep 26), **the entries
this file ships** flag 92% of the net growth in the top 20 rows, 87% of the top 50 and 81% of
the top 100 as not an employer's, and the staffing firms and job boards the tab hides hold
89/83/78% of it — decreasing, because the tail is endless. On 2026-09-25's window the list then
shipped flagged 86/72/61, and before that day's ten additions 59/49/42 (79/62/48 on
2026-09-21's). Those figures move with the list and must be
re-measured when names are added: the 45-entry draft in the research doc measured 73/52/40, and
quoting a number that describes a list nobody shipped is exactly the kind of borrowed fact this
repo has been caught by before. Method:
`docs/company-curation/2026-09-21_employer-type-classifier-measurement.md`.

Two consequences for anyone extending this:

* **Add names, do not add heuristics.** A regex that catches the tail will catch Cerebras with
  it. The bounded way to go deeper is to adjudicate the Boards actually displayed — a few new
  entrants a day — and record the verdicts here.
* **A label is not a verdict on the employer.** Capgemini and CI&T employ their own engineers;
  they are labelled `services` because their postings are client work, which is a different
  thing for a job hunter, not a worse company. Nothing is evicted from the index over this
  label (ADR-0053 and ADR-0083 own eviction); the tab filters, and says what it filtered.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from headstart.boards.board_identity import tenant

Operator = Literal["employer", "services", "staffing", "aggregator"]

#: Boards that re-post other companies' postings. They are not employers at all, so they are
#: separated from `services` — a user excluding staffing firms may still want Capgemini's own
#: roles, but nobody wants a posting served twice under a middleman's name.
AGGREGATORS: Final[frozenset[str]] = frozenset(
    {
        "jobgether",
        "jobsforlebanon",
        "jobrapido",
        "whatjobs",
        "trabajo",
        "jooble",
        # A job platform re-posting other employers' roles, nurses and call centres among
        # them (sampled live 2026-09-26).
        "jobsforhumanity",
    }
)

#: IT services, consulting and BPO operators, curated by hand from the top of the Expansion and
#: Rate lenses on 2026-09-21. They employ the people they post for, on client work, so the Hot
#: tab shows them, labelled. Compared against the Board's slug and its company name. Entries are
#: **exact normalized spellings** (see `_forms`): a multi-word operator is written as one run,
#: `Avance Consulting Services` -> "avanceconsultingservices". An entry that is also an ordinary
#: word or another company's name does not belong here — "ibex" was removed for matching Ibex
#: Medical Analytics. The same holds for STAFFING.
#:
#: Ordered by the group it came from so an entry can be argued with individually.
SERVICES: Final[frozenset[str]] = frozenset(
    {
        # India-headquartered IT services majors
        "wipro",
        "hcltech",
        "infosys",
        "cognizant",
        "tataconsultancy",
        "tcs",
        "techmahindra",
        "ltimindtree",
        "lntinfotech",
        "mphasis",
        "coforge",
        "mindtree",
        "hexaware",
        "birlasoft",
        "zensar",
        "cybage",
        "happiestminds",
        "sonatasoftware",
        "persistent",
        "itcinfotech",
        "conneqt",
        "virtusa",
        "qualitykiosk",
        # Global consultancies and their delivery arms
        "capgemini",
        "accenture",
        "accenturefederalservices",
        "avanade",
        "nttdata",
        "atos",
        "dxctechnology",
        "kyndryl",
        "unisys",
        "globant",
        "endava",
        "epam",
        "luxoft",
        "ciandt",
        "thoughtworks",
        "version1",
        "qualysoft",
        "agileengine",
        # BPO / CX operators
        "genpact",
        "wnsglobal",
        "firstsource",
        "teleperformance",
        "foundever",
        "sutherland",
        "concentrix",
        "alorica",
        "taskus",
    }
)

#: Staffing, contract-placement and talent-marketplace operators: their postings are placements
#: with clients, often one client contract posted in many cities, so the Hot tab hides them with
#: the job boards (ADR-0238). Split out of SERVICES on 2026-09-26; spelled as SERVICES is.
STAFFING: Final[frozenset[str]] = frozenset(
    {
        # Staffing and contract-placement firms
        "randstad",
        "adecco",
        "manpower",
        "kellyservices",
        "roberthalf",
        "teksystems",
        "insightglobal",
        "collabera",
        "computechcorporation",
        "ustechsolutions",
        "avanceconsultingservices",
        "righttalentrightnow",
        "maaniaconsultancyservices",
        "sonsoft",
        "sonsoftinc",
        "360itprofessionals",
        "360itprofessionalsinc",
        "bluelightconsulting",
        "artechinformationsystemllc",
        "artechinfosystems",
        "prosidianconsulting",
        "integratedresourcesinc",
        "infojini",
        "infojiniinc",
        "vtechsolution",
        "krgtechnologyinc",
        "tasqstaffingsolutions",
        "nityo",
        "diversant",
        "apexsystems",
        "modis",
        "experis",
        # Adjudicated from Hot's employer-labelled head on 2026-09-25, each by its own postings:
        # client IT contracts (USM, Atria, TecTammina, Jobsbridge, Idealforce's numbered
        # requisitions), warehouse temps (Stem Xpert), Gulf placements (VAMS: "…for Qatar"),
        # and local-job agencies (Squircle: "Female Accountant @ Vanasthalipuram"; Endeavor;
        # Weblee: "PVT BANKS RECRUITING").
        "squircleitconsultingservicespvtltd",
        "endeavoritsolution",
        "usm2",  # the slug: "usm" alone is also the University of Southern Mississippi
        "atriagroupllc",
        "tectammina",
        "stemxpert",
        "jobsbridge",
        "idealforcellc",
        "vams",
        "webleetechnologies",
        # Adjudicated from Hot's employer-labelled top 50 on 2026-09-26, each by a sample of its
        # own live postings: one client contract posted city by city (Dellfor: "AWS Cloud
        # Consultant" 518 times; AG Technologies: "Hiring Entry Level Software Engineer"), client
        # requisition codes (Xinnovit's "XIN001_…"), "local to"/"W2 only"/"in person interview"
        # contracts (Sonoma, Ask IT, 7th Sky), IT beside phlebotomists or plumbers (Mindlance,
        # US IT Solutions, Procom), and placements abroad (Urban Ridge: Doha, Cairo, Riyadh;
        # SBT Global: Korean-bilingual roles at clients' plants; Pragmatike, a recruiter).
        "deegitinc",
        "eproinc",
        "usitsolutionsinc",
        "sonomaconsultinginc",
        "mindlance",
        "infoways",
        "nextlevelbusinessservicesinc",
        "dellfortechnologies",
        "askitconsulting",
        "agtechnologies",
        "procomconsultantsgroup",
        "mapjects",
        "xinnovit",
        "pyramidit",
        "sbtglobalinc",
        "urbanridgesupplies",
        "brightvisiontechnologies",
        "maarut",
        "3coresystems",
        "7thskytechnologiesllc",
        "pragmatike",
        # The next tier, which hiding the first surfaced in Expansion's shown top 50, sampled
        # the same way: client contracts (Arete, EROS, SA Technologies, Career Guidant, LinkTag,
        # Paradigm Infotech, Comtech LLC, Procom Services, Implify, Veredus), a training-and-
        # placement mill (I.T. Excel: "QA and BA Training and Placement for OPT/CPT…"), a general
        # agency (Global Channel Management: data processors to graphic designers), tutors and
        # bakery managers in Lagos (Lextorah), a startup recruiter (Raydar), and freelance and
        # crowd-work marketplaces (FyerX, Welo Global). Quantix, sampled as staffing too, is
        # left out: "quantix" also names another company.
        "aretetechnologiesinc",
        "itexcelllc",
        "careerguidant",
        "erostechnologiesinc",
        "globalchannelmanagementinc",
        "satechnologiesinc",
        "implifyinc",
        "lextorahlds",
        "linktag",
        "procomservices",
        "comtechllc",
        "paradigminfotech",
        "veredusdc",
        "raydar",
        "fyerx",
        "weloglobal",
        # and warehouse, lab and payables temps beside IT contracts (AmNet, TekWissen,
        # BCforward), and client resourcing in Bengaluru (Intersoft KK).
        "amnetservicesinc",
        "tekwissenllc",
        "bcforward",
        "intersoftkk",
        # Talent marketplaces and placement programmes that post on behalf of others
        "eworgmbh",
        "simera",
        "turing",
        "andela",
        "toptal",
        "crossover",
    }
)

#: Exact forms that force ``employer`` regardless of what else matches. An operator's name is
#: sometimes a *substring of a different company's* name at whole-segment granularity, which no
#: matching rule can separate — only knowing the two companies can. Each entry names a real
#: collision found in the served population, so each is removable only by re-checking the data.
EXCEPTIONS: Final[frozenset[str]] = frozenset(
    {
        # A law firm, not the BPO "Sutherland" — `eversheds-sutherland` splits to both.
        "evershedssutherland",
        "eversheds",
        # A research institute, not the talent marketplace "Turing" — which is a real entry
        # (`greenhouse:turing`), so the collision cannot be fixed by dropping the entry.
        "alanturinginstitute",
        "turinginstitute",
    }
)

_SPLIT = re.compile(r"[^a-z0-9]+")
_TRAILING_DIGITS = re.compile(r"\d+$")


def _forms(text: str) -> set[str]:
    """Every spelling of ``text`` an entry may be compared against, as exact strings.

    Matching is **exact against one of these forms — never a substring, never a prefix.** Both
    looser rules were tried against the real 33,480-Board population and both mislabelled real
    employers. Substring matching calls every "…Manufacturing" board a services firm, because
    "manufacturing" contains "turing". Prefix matching calls **Ibex Medical Analytics** a BPO,
    **TopTalents** an instance of Toptal and **Zensark Tecnologies** an instance of Zensar —
    three separate companies wrongly demoted by three characters of overlap.

    The forms are the alphanumeric runs (`careers.wipro.com` → `wipro`), the whole string with
    separators removed (`Avance Consulting Services` → `avanceconsultingservices`, which is how
    a multi-word operator is written as one entry), and each of those with a trailing number
    dropped, because SmartRecruiters appends a disambiguating digit to a slug it has seen before
    (`Collabera6`, `Atos1`).
    """
    lowered = text.lower()
    parts = [p for p in _SPLIT.split(lowered) if p]
    forms = {*parts, "".join(parts)}
    forms |= {_TRAILING_DIGITS.sub("", f) for f in forms}
    return {f for f in forms if f}


def classify(board_key: str, company: str | None = None) -> Operator:
    """The operator behind this Board, defaulting to ``employer``.

    Both the ``board_key`` and the Board's stated company name are read, because neither alone
    is reliable: only ~20% of served rows resolve a real company name (ADR-0114), while a slug
    is often a host that names the operator plainly (`careers.wipro.com`). Aggregators are
    tested first — a Board that is both re-posting and a services firm is more usefully called
    the former — then staffing firms, then services firms.

    ``employer`` is the default on purpose. The measurement in the module docstring says a rule
    confident enough to move an *unrecognised* Board out of that default does not exist, so an
    unknown Board is served as an employer and the list stays recall-biased in the repo's own
    direction: a services firm slipping through is a worse row, while an employer wrongly
    demoted is a job nobody sees.
    """
    forms = _forms(tenant(board_key)) | _forms(company or "")
    if forms & EXCEPTIONS:
        return "employer"
    if forms & AGGREGATORS:
        return "aggregator"
    if forms & STAFFING:
        return "staffing"
    if forms & SERVICES:
        return "services"
    return "employer"

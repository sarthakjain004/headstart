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
2026-09-21's). On 2026-09-29's window it flags 28/23/20 (the hidden Operators 18/14/12), against 26/20/17 (16/11/8)
before that day's entries; the MCP critique's round-4 entries of the same day name no company
in the lens's top 100, so they leave these figures as they were. On 2026-09-30's window (Sep 23 →
Sep 30) it flags 31/33/29 (hidden 26/23/20), against 28/26/23 (22/20/17) before the MCP
critique's round-5 entries, eight of which sit in the lens's top 100. Those figures move with the list and must be
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
from collections.abc import Iterable
from typing import Final, Literal, get_args

from headstart.boards.board_identity import tenant

Operator = Literal["employer", "services", "staffing", "aggregator"]

#: Every Operator, in :data:`Operator`'s order: the values a search's ``operators`` keeps (ADR-0335).
OPERATORS: Final[tuple[str, ...]] = get_args(Operator)

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
        # Adjudicated 2026-09-29 by five live postings each (ADR-0335): a Sri Lankan job board
        # re-posting Codification's, InSync's and Triolem's roles, and a WP Job Openings site
        # posting cashiers beside ad-server and CPU-design engineers under one invented name
        # ("Chimney Sweep Masters" on board.vals.services).
        "findmyjoblk",
        "boardvalsservices",
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
        # Consultancies on Hot's employer-labelled head of 2026-09-29, each by five live
        # postings (ADR-0335): a life-sciences IT consultancy's client projects (Fusion) and
        # ABeam's Singapore consultants.
        "fusionconsulting",
        "abeamconsultingsingapore",
        # Round 5 of the MCP critique (2026-09-30): the IT services and engineering-services
        # firms labelled employer in hiring_now's top 30 on each Lens, each by five live
        # postings. "Our client's most trusted technology partner" (Iris Software); "a digital
        # technology service provider… partner of choice for… Fortune 1000 companies" (Brillio);
        # "join our client Samsung…" (Xoriant); AWS work "tailored to client requirements"
        # (Encora); "a global provider of… digital, and cloud services", on client sites
        # (Mastek); "an engineering services provider" (Quest Global); "a global IT managed
        # services firm" (Milestone Technologies); client-coded titles and "projects with leading
        # global clients" (Software Mind); "an applied AI engineering firm… for our clients"
        # (Robots and Pencils); client proofs of concept, an analytics BPO like Genpact and WNS
        # (EXL); client SAP support at "All PWC Locations" (Elfonze). Joined forms where a part
        # is a word: "irissoftware", "questglobal", "softwaremind", "milestonetechnologiesinc".
        "irissoftware",
        "brillio",
        "xoriant",
        "encora",
        "mastek",
        "questglobal",
        "milestonetechnologiesinc",
        "softwaremind",
        "robotsandpencils",
        "exl",
        "elfonze",
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
        # Consultant" 518 times), client
        # requisition codes (Xinnovit's "XIN001_…"), "local to"/"W2 only"/"in person interview"
        # contracts (Sonoma, Ask IT, 7th Sky), IT beside phlebotomists or plumbers (Mindlance,
        # US IT Solutions, Procom), and placements abroad (Urban Ridge: Doha, Cairo, Riyadh;
        # SBT Global: Korean-bilingual roles at clients' plants; Pragmatike, a recruiter).
        "deegitinc",
        "eproinc",
        "usitsolutionsinc",
        "sonomaconsultinginc",
        "mindlance",
        "nextlevelbusinessservicesinc",
        "dellfortechnologies",
        "askitconsulting",
        "procomconsultantsgroup",
        "mapjects",
        "xinnovit",
        "pyramidit",
        "sbtglobalinc",
        "urbanridgesupplies",
        "brightvisiontechnologies",
        "maarutinc",  # the Zoho tenant: "maarut" alone is a word other names use
        "3coresystems",
        "7thskytechnologiesllc",
        "pragmatike",
        # The next tier, which hiding the first surfaced in Expansion's shown top 50, sampled
        # the same way: client contracts (Arete, EROS, SA Technologies, Career Guidant, Paradigm
        # Infotech, Comtech LLC, Procom Services, Implify, Veredus), a training-and-placement mill
        # (I.T. Excel: "QA and BA Training and Placement for OPT/CPT…"), a general agency (Global
        # Channel Management: data processors to graphic designers), tutors and bakery managers
        # in Lagos (Lextorah), and freelance and crowd-work marketplaces (FyerX, Welo Global).
        # Sampled as staffing too but left out, each a single word another company's name can
        # carry and with no form of its own to narrow to: Quantix, Info-Ways ("X Infoways Pvt
        # Ltd"), LinkTag, Raydar, and AG Technologies (a second Workable Board of that name).
        "aretetechnologiesinc",
        "itexcelllc",
        "careerguidant",
        "erostechnologiesinc",
        "globalchannelmanagementinc",
        "satechnologiesinc",
        "implifyinc",
        "lextorahlds",
        "procomservices",
        "comtechllc",
        "paradigminfotech",
        "veredusdc",
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
        "simeratalent",  # not "simera": Simera Sense (satellite optics) is an employer
        "turing",
        "andela",
        "toptal",
        "crossover",
        # Adjudicated from the four Lenses' top 100s of 2026-09-29, each by five live postings
        # (ADR-0335): psychologists in Oman beside Salesforce developers (Vrinda); a masked
        # client's construction and 6-month IT contracts (Flintex); "engineering talent
        # solutions" (Gramian); "2COMS Payroll… for a leading MNC" (2COMS); unnamed startups'
        # founding roles (Clera); 6-12 month client roles (Astra North); W2/C2C rates and a
        # named client (Inabia); AI data-collection crowd work, as Welo Global (TSMG); "for one
        # of Weekday's clients" (Weekday AI); "(Fastwater Staffing)… Our client" (HIKINEX, whose
        # Board is `breezy:recruiting`); $80-90/hr contracts (OmegaHires); "for our renowned IT
        # client… share your CV" (Technopride); and the recruiters whose WP Job Openings sites
        # led a DevOps requirements sample: Dawn InfoTek ("recruiting all levels of IT positions
        # for our clients"), ACME HR Consulting and Angel and Genie (CTC in every title).
        "vrindainternational",
        "flintex",
        "gramianconsultinggroup",
        "2coms",
        "clera",
        "astranorthinfoteckinc",
        "inabia",
        "tsmg",
        "weekdayai",
        "hikinex",
        "omegahires",
        "technopride",
        "dawninfotek",
        "acmehr",
        "angelandgenie",
        # Round 4 of the MCP critique (2026-09-29, ADR-0335): the firms that led search rows
        # and requirements samples as employers, each by five live postings, or every posting
        # where it has fewer. "Our client" or an unnamed client (RemoteStar, WhyHireWrong?, Zero
        # to One search, All About Expats, Cross Border Talents, RedTech, OnHires, Nakunj, Attain
        # Talent, Flex On-Demand, Pakistan Recruitment, Huntress Talent, HRBaires, deCircle's
        # client-prefixed titles, Hunt St, Workster: "partnering with… to recruit"); Nike roles in
        # Beaverton (BizTek People); "on behalf of one of our prestigious Fortune 500… clients"
        # (Knowfinity); an HCLTech posting (OctoRudra); a posting written as Claroty's
        # (Globaldev); W2 rates and 6-12 month contracts (GovServicesHub, TheCorporate, KGS);
        # "Permanent (Talpro)… CTC Offered" (Talpro); anonymised templates across a dozen
        # cities (viraaj, Umanist, CLIQHR); AI-training contract work in eight countries (YO HR);
        # and firms whose own sites say so: "a leading IT staffing company" (DigitalXNode), "a
        # recruitment and consulting company" (Sperton), "nearshore staff augmentation" (Helix
        # Workforce), "a niche recruitment company" (Sabenza IT), "recruiting, and deploying
        # technology professionals" (STAFIDE), "we'll show up with applicants" (Hire Hangar),
        # "we connect… offshore professionals… with global businesses" (Remotely), and Two95's
        # client team details with "We also pay for referals". Joined forms where a part is a
        # word or another name: "huntresstalent" (Huntress is a security company), "attaintalent"
        # (Attain is a consultancy), "joinremotely", "sabenzait", "helixworkforce".
        "digitalxnode",
        "remotestar",
        "sperton",
        "hirehangar",
        "whyhirewrong",
        "umaniststaffingllc",
        "helixworkforce",
        "viraajhrsolutions",
        "two95",
        "knowfinityacademyllp",
        "octorudrahrllp",
        "zerotoonesearch",
        "stafide",
        "sabenzait",
        "allaboutexpats",
        "govserviceshub",
        "worksterjobs",
        "cliqhr",
        "huntst",
        "decircletalentpartner",
        "crossbordertalents",
        "redtechrecruitmentltd",
        "onhires",
        "biztekpeople",
        "flexondemand",
        "yohrconsultancy",
        "pakistanrecruitment",
        "huntresstalent",
        "attaintalent",
        "thecorporatellc",
        "nakunj",
        "kgstechnologygroupinc",
        "joinremotely",
        "hrbaires",
        "globaldevgroup",
        "talproindia",
        # Round 5 of the MCP critique (2026-09-30), from the same top 30s, each by its live
        # postings: "a leading provider of nearshore staff augmentation services… Our client
        # is…" (Truelogic); "We're partnering with a company that…" for an unnamed client's CTO
        # (Breakmark); "BizFirst is assisting our client with recruiting" (BizFirst); and end
        # clients' requisitions passed through (Algoleap): Deloitte's own text in 3 of its 196
        # postings, DHL's "Specific Remarks/Requirement by customer", demand codes in titles
        # ("(D239)") with "only Immediate joiners", and client-voiced posts ("map it to our
        # ecosystem"), each label re-read on 5 or more postings on 2026-09-30 (ADR-0370); its
        # own site calls it a product engineering firm, so it is the least settled of the four.
        # Zorba Consulting India stays off the list, as ADR-0335 left it: its
        # postings ("we are looking for a Lead-level resource") still do not settle it.
        "truelogic",
        "breakmark",
        "bizfirst",
        "algoleap",
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
        # Found checking every entry against the directory and the liveness ledgers (review of
        # #731): a robotics startup, a medical-device maker and a trade-union club.
        "turingmachinesinc",
        "atosmedical",
        "atosmedicalus",
        "sutherlanddistricttradeunionclub",
    }
)

#: Employers adjudicated from their own postings whose names still read like an agency's
#: (:func:`unverified`), spelled as SERVICES is, so `hiring_now` stops flagging them. Not a
#: label: `classify` never reads it (ADR-0335).
VERIFIED_EMPLOYERS: Final[frozenset[str]] = frozenset(
    {
        # 2026-09-29, five live postings each: Air Force test engineers under the TMAS III
        # contract (Odyssey), a federal contractor's analysts in Bethesda (Black Canyon), and
        # MSX International's own automotive helpdesk engineers.
        "odysseysystemsconsultinggroupltd",
        "blackcanyonconsulting",
        "msxinternational",
    }
)

#: The words that make a company's name read like a staffing firm's or a recruiter's, each at
#: the start of a word. A lead, never a label: of the 12 employer-labelled companies it named on
#: the Hiring now tab of 2026-09-29, 4 were agencies, 3 could not be told from their postings,
#: and 5 were a defence contractor, a government contractor, two consultancies and an
#: automotive services firm (ADR-0335). The
#: module docstring's measurement found the same of name vocabulary: too loose to label by.
_AGENCY_NAME = re.compile(
    r"(?<![a-z0-9])(?:consult\w*|staffing|recruit\w*|hr|manpower|placements?|talents?"
    r"|international)(?![a-z0-9])"
)

#: Host suffixes an ATS vendor serves many tenants under, each recurring across at least 27
#: Scrapable Boards' tenants on 2026-09-30 (ADR-0366). The labels before one are the tenant's
#: own; the suffix is the vendor's, and SAP's `hr` in `lockheed.jobs.hr.cloud.sap` flagged all
#: 82 of its Boards as agencies.
_VENDOR_HOST = re.compile(
    r"\.(?:jobs\.hr\.cloud\.sap|zohorecruit\.[a-z.]+|icims\.com|oraclecloud\.com"
    r"|(?:cluster\d+\.)?openings\.co|taleo\.net|eightfold\.ai|jobs2web\.com"
    r"|myworkdayjobs\.com|successfactors\.(?:com|eu))$"
)

#: Second-level labels a country registry sells under (`isuzu.co.jp`, `nrc-cnrc.gc.ca`), so the
#: registrable label is the one before them.
_REGISTRY_LABELS = frozenset(
    {"co", "com", "org", "net", "gov", "gc", "ac", "edu", "or", "ne", "go"}
)


def _own_label(host_or_slug: str) -> str:
    """The part of a Board's tenant that its company chose (ADR-0366): a slug whole, the labels
    before a vendor's host suffix, else a host's registrable label. `recruit.lg.com` is LG's
    recruiting site and `lockheed.jobs.hr.cloud.sap` Lockheed's SAP one; neither says "hr" or
    "recruit" of the company, while `hr-path.com` and `3m-consultancy.zohorecruit.com` do."""
    host = host_or_slug.lower()
    if "." not in host:
        return host
    vendor = _VENDOR_HOST.search(host)
    if vendor:
        return host[: vendor.start()]
    labels = host.split(".")
    if len(labels) > 2 and labels[-2] in _REGISTRY_LABELS:
        return labels[-3]
    return labels[-2]


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


def company_operator(boards: Iterable[str], name: str) -> Operator:
    """Who runs a company (ADR-0171, ADR-0238): an aggregator if any of its ``boards`` re-posts,
    else staffing if any places staff with clients, else services if any does client IT work,
    else the employer. The directory stage writes it, and the Space decides it again when it
    loads the directory, so what Hot hides never waits on the next run to write the file."""
    found = {classify(board, name) for board in boards}
    return next(
        (op for op in ("aggregator", "staffing", "services") if op in found),
        "employer",
    )


def unverified(boards: Iterable[str], name: str) -> bool:
    """Whether a company is an employer only by default and its name, or a Board's own label
    (:func:`_own_label`), reads like an agency's (``_AGENCY_NAME``): nobody has read its
    postings, and its name says someone
    should (ADR-0335). Vrinda International ranked third on the Hiring now tab as an employer
    while posting clinical psychologists in Oman. A company on any list here, as an Operator, an
    exception or a verified employer, is not unverified."""
    boards = list(boards)
    if company_operator(boards, name) != "employer":
        return False
    forms = _forms(name).union(*(_forms(tenant(board)) for board in boards))
    if forms & (EXCEPTIONS | VERIFIED_EMPLOYERS):
        return False
    texts = [name.lower(), *(_own_label(tenant(board)) for board in boards)]
    return any(_AGENCY_NAME.search(text) for text in texts)

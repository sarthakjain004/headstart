"""Title rules that decide a served row's role family before its centroid is consulted (ADR-0215).

The trends taxonomy used to file every row by its nearest frozen centroid alone (ADR-0040). Those
centroids were fitted on embeddings of title *plus full description*, so employer boilerplate and
industry decided the family as much as the role did. On 250 served Jobs drawn uniformly and
hand-labelled after these rules were frozen (2026-09-25), nearest-centroid got 60.0% right; these
rules first, with the centroid only for titles no rule decides, got 70.8%. The rules decided 75%
of those Jobs, at 79.3% precision. Over 22,656 pairs of copies of one Job, the centroid
put the two copies in different families 11.9% of the time and the rules-first order 2.7%.
Every agency coder and job platform surveyed classifies the title first (``docs/role-families/``).

Shaped like :mod:`headstart.tech_filter` (ADR-0017, ADR-0068). For one title:

1. every family cue that matches is collected with its precedence class, strength and position;
2. a **negative** rule (a non-software discipline, trade, retail or sales word) decides
   ``non-tech`` unless a *strong* cue also matched. A weak cue (project manager, engineering
   manager, test engineer, architect, ...) does not override it: "Engineering Manager -
   Substation" is civil engineering;
3. otherwise the lowest precedence class wins: people management > product/program management >
   support > specialty > language > cloud platform > web stack > level and architect > generic
   software. Within one class, the cue named first in the title wins, and a cloud-provider word
   outranks the stack and generic words ("Backend Engineer (AWS)" is cloud-infrastructure);
4. nothing matched: no decision, and the caller falls back to the centroid.

The title only, never the department: a department names the org, not the role (ADR-0068), and a
department tier decided 0.7% of rows at unmeasurable precision in the bake-off.

Every decision names its tier and rule, so "why is this Job in data-engineering?" has an
answer a person can check.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import NamedTuple

from headstart.roles import NON_TECH

# Bumped by hand only for a rule change that moves enough rows to re-base every Trends series,
# the way a centroid refit does (ADR-0040). The Trends chart plots only the newest series
# version, so a bump restarts the chart. A routine edit (a counter-rule for a newly found false
# match) is not a bump: `fingerprint()` changes by itself, and the epoch ledger marks that tick
# (ADR-0164).
RULES_GENERATION = 1

# Precedence classes; lower wins.
(
    _PEOPLE_MANAGEMENT,
    _PRODUCT_PROGRAM,
    _SUPPORT,
    _SPECIALTY,
    _LANGUAGE,
    _CLOUD_PLATFORM,
    _WEB_STACK,
    _LEVEL_AND_ARCHITECT,
    _GENERIC_SOFTWARE,
) = range(1, 10)


@dataclass(frozen=True)
class _Cue:
    family: str
    name: str
    pattern: re.Pattern
    rank: int
    strong: bool  # a strong cue survives a negative rule


def _any_of(terms: list[str]) -> str:
    """A regex matching any one of ``terms``, kept as a list so each term reads and diffs alone
    (the way ``tech_filter`` writes its terms)."""
    return "|".join(terms)


def _any_word(words: list[str], *, closed: bool = True) -> str:
    r"""Any one of ``words`` after a word boundary: ``\b(a|b|...)``, closed by a second ``\b``
    unless ``closed`` is false, where a stem like "incident respon" must match "response"."""
    return r"\b(" + _any_of(words) + (r")\b" if closed else ")")


def _cue(family: str, name: str, pattern: str, rank: int, strong: bool = True) -> _Cue:
    return _Cue(family, name, re.compile(pattern, re.IGNORECASE), rank, strong)


_CUES: tuple[_Cue, ...] = (
    # people management — weak: "Engineering Manager - Substation" is civil
    _cue(
        "engineering-management",
        "engineering-manager",
        _any_of(
            [r"\bengineering manager\b", r"\bmanager,? (of )?(software )?engineering\b"]
        ),
        _PEOPLE_MANAGEMENT,
        strong=False,
    ),
    _cue(
        "engineering-management",
        "software-dev-manager",
        _any_of(
            [
                r"\b(software|application|applications|apps) (development|engineering) (manager|director)\b",
                r"\bdevelopment manager\b",
                r"\bdev manager\b",
            ]
        ),
        _PEOPLE_MANAGEMENT,
    ),
    _cue(
        "engineering-management",
        "director-head-vp-engineering",
        _any_of(
            [
                r"\b(director|head|vp|vice president|svp|avp)\b[^|]{0,15}\b(software|platform|data|cloud|infrastructure|security|ai|ml|devops|qa|r&d)?\s*engineering\b",
                r"\bdirector of (r&d|software)\b",
                r"\bengineering director\b",
            ]
        ),
        _PEOPLE_MANAGEMENT,
        strong=False,
    ),
    _cue(
        "engineering-management",
        "cto-cio",
        _any_of(
            [
                r"\bcto\b",
                r"\bcio\b",
                r"chief (technology|information|technical|digital) officer",
            ]
        ),
        _PEOPLE_MANAGEMENT,
    ),
    _cue(
        "engineering-management",
        "it-manager",
        _any_of(
            [
                r"\bit (manager|director)\b",
                r"\b(manager|director|head),? (of )?(it|information technology)\b",
                r"\bit infrastructure manager\b",
                r"\bmanager,? it infrastructure\b",
            ]
        ),
        _PEOPLE_MANAGEMENT,
    ),
    _cue(
        "engineering-management",
        "infrastructure-director",
        r"\b(director|head|vp|vice president)\b,? (of )?(it )?infrastructure\b",
        _PEOPLE_MANAGEMENT,
    ),
    _cue(
        "engineering-management",
        "qa-test-manager",
        _any_of(
            [
                r"\b(qa|test|quality assurance) manager\b",
                r"\bmanager,? (qa|quality assurance)\b",
            ]
        ),
        _PEOPLE_MANAGEMENT,
    ),
    # product / program / project / delivery management
    _cue(
        "product-management",
        "product-manager-owner",
        _any_of(
            [
                r"\bproduct (manager|owner|management|lead|director)\b",
                r"\bhead of product\b",
                r"\bvp,? product\b",
            ]
        ),
        _PRODUCT_PROGRAM,
        strong=False,
    ),
    _cue(
        "product-management",
        "technical-program-manager",
        _any_of(
            [
                r"\btechnical program manager\b",
                r"\btpm\b",
                r"\btechnical project manager\b",
                r"\bit (project|program|delivery) manager\b",
                r"\bdelivery (manager|lead)\b",
                r"\bscrum master\b",
                r"\bagile coach\b",
                r"\brelease manager\b",
            ]
        ),
        _PRODUCT_PROGRAM,
        strong=False,
    ),
    _cue(
        "product-management",
        "program-project-manager",
        _any_of(
            [r"\b(program|project) manager\b", r"\bprogram lead\b", r"\bproject lead\b"]
        ),
        _PRODUCT_PROGRAM,
        strong=False,
    ),
    # specialties
    _cue(
        "qa-test",
        "qa-test",
        _any_of(
            [
                r"\bqa\b(?!\s*/\s*qc)",
                r"quality assurance",
                r"\bsdet\b",
                r"software development engineer in test",
                r"\btest automation\b",
                r"\bautomation test",
                r"\bsoftware test",
                r"\btester\b",
                r"\bsqa\b",
                r"\btest analyst\b",
                r"\bsoftware quality\b",
                r"\bqe\b",
                r"quality engineering(?! manager)",
                r"\btesting engineer\b(?=.*\b(software|automation|qa|manual)\b)",
                r"\bautomation engineer\b.*\bsdet\b",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "qa-test",
        "test-engineer",
        _any_of(
            [
                r"\btest engineer\b",
                r"\btesting engineer\b",
                r"\bvalidation engineer\b(?=.*\bsoftware\b)",
            ]
        ),
        _SPECIALTY,
        strong=False,
    ),
    _cue(
        "security-engineering",
        "security",
        _any_word(
            [
                r"cyber\s?security",
                r"cyber",
                r"information security",
                r"information systems security",
                r"systems security officer",
                r"infosec",
                r"appsec",
                r"application security",
                r"security (engineer|analyst|architect|operations|researcher)",
                r"soc (analyst|engineer|l[123])",
                r"penetration test",
                r"pen ?tester",
                r"red team",
                r"vulnerability",
                r"threat",
                r"\biam\b",
                r"identity (and|&) access",
                r"\bgrc\b",
                r"\bisso\b",
                r"\bisse\b",
                r"\bciso\b",
                r"devsecops engineer",
                r"security engineering",
                r"\bwaf\b",
                r"detection engineer",
                r"information assurance",
                r"incident respon",
            ],
            closed=False,
        ),
        _SPECIALTY,
    ),
    _cue(
        "devops",
        "devops",
        _any_of(
            [
                r"\bdev ?(sec)?ops\b",
                r"\bbuild (and|&) release",
                r"\brelease engineer",
                r"\bci ?/ ?cd\b",
                r"\bconfiguration management engineer",
                r"infrastructure automation",
                r"infrastructure as code",
                r"\bterraform\b",
                r"\bansible\b",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "sre-platform",
        "sre-platform",
        _any_of(
            [
                r"site reliability",
                r"\bsre\b",
                r"\bplatform engineer",
                r"\bplatform architect\b",
                r"\bproduction engineer\b",
                r"\bobservability\b",
                r"\breliability engineer\b(?=.*\b(site|software|platform|infrastructure|cloud)\b)",
                r"\bkubernetes\b",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "cloud-infrastructure",
        "cloud",
        _any_of(
            [
                r"\bcloud (engineer|engineering|architect|architecture|developer|infrastructure|platform|administrator|admin|consultant|operations|ops|specialist|solutions?|systems|support|engineer)\b",
                r"\bcloudops\b",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "cloud-infrastructure",
        "cloud-provider",
        _any_of(
            [
                r"\bcloud\b(?!\s+(sales|account|marketing))",
                r"\baws\b",
                r"\bazure\b(?!\s*devops)",
                r"\bgcp\b",
                r"google cloud",
            ]
        ),
        _CLOUD_PLATFORM,
        strong=False,
    ),
    _cue(
        "data-engineering",
        "data-engineering",
        _any_of(
            [
                r"\bdata (engineer|engineering|architect|architecture|modell?er|modeling|platform|warehouse|pipeline|management engineer|developer)\b",
                r"\betl\b",
                r"\belt\b",
                r"big data",
                r"analytics engineer",
                r"\bsnowflake\b",
                r"\bdatabricks\b",
                r"\binformatica\b",
                r"\bteradata\b",
                r"\bspark\b",
                r"\bsql developer\b",
                r"\bpl/?sql\b",
                r"\bdata integration\b",
                r"\bsemantic data\b",
                r"\bdatabase architect\b",
                r"\bdata management\b(?!.*clinical)",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "data-science",
        "data-science",
        _any_of(
            [
                r"data scien(ce|tist)",
                r"\bstatistician\b",
                r"decision scientist",
                r"quantitative (analyst|researcher)",
                r"\bbioinformatic",
            ]
        ),
        _SPECIALTY,
    ),
    # "AI" names a company's product, not the role, in "AI-first"/"AI-powered"/... titles
    _cue(
        "ai-ml",
        "ai-ml",
        _any_of(
            [
                r"machine learning",
                r"\bml\b",
                r"\bai\b(?![- ](first|native|powered|driven|enabled|company|startup|finance agent|neobank|investing|stockbroking))",
                r"\bai/ml\b",
                r"\baiml\b",
                r"artificial intelligence",
                r"\bmlops\b",
                r"\bllm",
                r"generative ai",
                r"\bgen ?ai\b",
                r"agentic",
                r"computer vision",
                r"deep learning",
                r"\bnlp\b",
                r"applied scientist",
                r"research (scientist|engineer)\b(?=.*\b(ai|ml|machine|learning|model|pre-?training|inference|llm|video|speech)\b)",
                r"\bpre-?training\b",
                r"\binference\b",
                r"reinforcement learning",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "data-analytics",
        "data-analytics",
        _any_of(
            [
                r"\bdata analy(st|tics)\b",
                r"\b(business|systems?|reporting|marketing|product|bi|financial data|clinical data) analyst\b",
                r"\bbusiness (intelligence|systems analyst)\b",
                r"\bbi (developer|engineer|architect)\b",
                r"power ?bi",
                r"\btableau\b",
                r"\bqlik\b",
                r"\blooker\b",
                r"\bcognos\b",
                r"\bsas programmer\b",
                r"\banalytics\b(?! engineer)",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "mobile-development",
        "mobile",
        _any_of(
            [
                r"\bandroid\b",
                r"\bios\b",
                r"\bmobile\b",
                r"\bflutter\b",
                r"react native",
                r"\bswift\b",
                r"\bkotlin\b(?=.*\bandroid\b)",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "hardware-embedded",
        "hardware-embedded",
        _any_of(
            [
                r"\bfirmware\b",
                r"\bembedded\b",
                r"\basic\b",
                r"\bfpga\b",
                r"\brtl\b",
                r"design verification",
                r"\bdv engineer",
                r"physical design",
                r"\bvlsi\b",
                r"\bsilicon\b",
                r"\banalog\b",
                r"\bmixed[- ]signal\b",
                r"\brf (design|ic|engineer)",
                r"\bmmic\b",
                r"\bdft\b",
                r"design for test",
                r"microarchitect",
                r"\bcpu\b",
                r"\bgpu\b(?=.*\b(architect|design|verification|hardware)\b)",
                r"\bsignal (and|&) power integrity\b",
                r"\bpcb\b",
                r"\belectronics?\b(?! technician)",
                r"hardware (engineer|design|validation|test)",
                r"\bbios\b",
                r"\bsoc\b(?=.*\b(design|verification|architect)\b)",
                r"\bdriver(s)? (engineer|developer)\b",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "network-infrastructure",
        "network",
        _any_of(
            [
                r"\bnetwork(ing)? (engineer|administrator|admin|architect|technician|specialist|analyst|consultant|operations)",
                r"\bnoc\b",
                r"\bccie\b",
                r"\bccnp\b",
                r"\bdns\b",
                r"\bwan\b",
                r"\blan\b",
                r"telecom(munication)?s? (engineer|network)",
                r"\bwireless (network )?engineer\b",
                r"network (and|&) (telecom|security)",
                r"\bnetworking\b",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "it-operations",
        "it-operations",
        _any_of(
            [
                r"\b(system|systems|sys|database|linux|unix|windows|wintel|server|storage|backup|vmware|citrix|exchange|m365|o365|office 365|active directory|middleware)\s?(administrator|admin|operations)\b",
                r"\b(database|linux|unix|windows|wintel|server|storage|backup|vmware|citrix|exchange|m365|o365|middleware)\s?engineer\b",
                r"\bactive directory\b",
                r"\bentra( id)?\b",
                r"\b(m365|o365|office 365)\b",
                r"\bbackup\b",
                r"\bdatacenter\b",
                r"\bit infra",
                r"\bsystems? engineer\b(?=.*\b(windows|linux|unix|vmware|citrix|microsoft|wintel|server|infrastructure|kvm|virtuali[sz]ation)\b)",
                r"\bdba\b",
                r"\bsysadmin\b",
                r"data cent(er|re) (technician|engineer|operations)",
                r"\bdatacenter technician\b",
                r"\bit operations\b",
                r"\binfrastructure (engineer|specialist|analyst)\b",
                r"\bcompute engineer\b",
                r"\bendpoint\b",
                r"\bintune\b",
                r"\bsccm\b",
                r"\bexalogic",
            ]
        ),
        _SPECIALTY,
        strong=False,
    ),
    _cue(
        "it-support",
        "it-support",
        _any_of(
            [
                r"help ?desk",
                r"desktop support",
                r"desk ?side",
                r"\bit support\b",
                r"technical support",
                r"tech support",
                r"service desk",
                r"\bsupport (engineer|analyst|specialist|technician|developer|representative)\b",
                r"application support",
                r"production support",
                r"\bapps? sup\b",
                r"\bl[123] support\b",
                r"\bit (specialist|technician|intern|helpdesk)\b",
                r"\bcomputer (field )?technician\b",
                r"\bfield serviced? tech\b",
            ]
        ),
        _SUPPORT,
    ),
    _cue(
        "enterprise-platform",
        "enterprise-platform",
        _any_of(
            [
                r"\bsap\b",
                r"\bs/?4 ?hana\b",
                r"\bhana\b",
                r"\babap\b",
                r"\bfiori\b",
                r"salesforce",
                r"servicenow",
                r"\bworkday\b(?!\s+(sales|account))",
                r"dynamics 365",
                r"\bd365\b",
                r"microsoft dynamics",
                r"dynamics (crm|ax|nav|gp|f&o|finance)",
                r"\bnetsuite\b",
                r"oracle (ebs|fusion|erp|hcm|financials|cloud|epm|apps|applications)",
                r"\boracle\b(?=.*\b(consultant|functional|erp|finance|analyst)\b)",
                r"peoplesoft",
                r"guidewire",
                r"\bpega\b",
                r"mulesoft",
                r"\btibco\b",
                r"\bboomi\b",
                r"\bapigee\b",
                r"\baxway\b",
                r"datapower",
                r"\bhybris\b",
                r"power platform",
                r"power apps",
                r"\bteamcenter\b",
                r"\bwindchill\b",
                r"\bepic\b(?=.*\b(analyst|consultant|application|systems)\b)",
                r"\batlassian\b",
                r"\bjira admin",
                r"\bsharepoint\b",
                r"\bappian\b",
                r"\bjd ?edwards\b",
                r"\bjde\b",
                r"\bmedidata\b",
                r"\bveeva\b",
            ]
        ),
        _SPECIALTY,
    ),
    _cue(
        "systems-engineering",
        "systems-engineering",
        _any_of(
            [
                r"\bmbse\b",
                r"model[- ]based systems",
                r"systems engineering(?! manager)",
                r"\bsystems? (integration|verification)\b",
                r"\bv&v\b",
                r"\bsystems architect\b",
                r"\bsystem engineer\b(?=.*\b(defen[cs]e|aerospace|missile|space|satellite|radar|automotive|avionics)\b)",
            ]
        ),
        _SPECIALTY,
        strong=False,
    ),
    # languages: the first named decides
    _cue(
        "java-development",
        "java",
        _any_of([r"\bjava(?!\s*script)", r"\bj2ee\b", r"\bspring boot\b"]),
        _LANGUAGE,
    ),
    _cue(
        "python-development",
        "python",
        _any_of([r"\bpython\b", r"\bdjango\b", r"\bflask\b", r"\bfastapi\b"]),
        _LANGUAGE,
    ),
    _cue(
        "web-development",
        "web-language",
        _any_of(
            [
                r"\.net\b",
                r"\bdot ?net\b",
                r"\bc#",
                r"\basp\.net\b",
                r"\bphp\b",
                r"\blaravel\b",
                r"\bjavascript\b",
                r"\btypescript\b",
                r"\breact(\.?js)?\b",
                r"\bangular(js)?\b",
                r"\bvue(\.?js)?\b",
                r"\bnode(\.?js)?\b",
                r"\bnext\.?js\b",
                r"\bwordpress\b",
                r"\bdrupal\b",
                r"\bshopify\b",
                r"\bmagento\b",
                r"\bmegento\b",
                r"\baem\b",
                r"adobe experience manager",
                r"\bmern\b",
                r"\bmean stack\b",
            ]
        ),
        _LANGUAGE,
    ),
    _cue(
        "software-engineering",
        "other-language",
        _any_of(
            [
                r"\bc\+\+",
                r"\bgolang\b",
                r"\bgo\b(?= (developer|engineer))",
                r"\(go\)",
                r"\brust\b",
                r"\bscala\b",
                r"\bruby\b",
                r"\bcobol\b",
                r"\bmainframe\b",
                r"\bhlasm\b",
                r"\belixir\b",
                r"\bhaskell\b",
                r"\bcuda\b",
            ]
        ),
        _LANGUAGE,
    ),
    # web-stack words
    _cue(
        "web-development",
        "web-stack",
        _any_of(
            [
                r"full[\s-]?stack",
                r"\bfullstack\b",
                r"front[\s-]?end (developer|engineer|software|web)",
                r"\bfrontend\b",
                r"\bweb (developer|development|engineer|designer)\b",
                r"\bui (developer|engineer)\b",
                r"\bwebmaster\b",
            ]
        ),
        _WEB_STACK,
    ),
    # level-only titles and architects
    _cue(
        "architecture",
        "architect",
        _any_of([r"\barchitect\b", r"\barchitecture\b"]),
        _LEVEL_AND_ARCHITECT,
        strong=False,
    ),
    _cue(
        "architecture",
        "presales-solutions-engineer",
        _any_of(
            [
                r"\bsolutions? engineer\b",
                r"\bsales engineer\b",
                r"\bpre-?sales\b",
                r"\bsolutions? consultant\b",
            ]
        ),
        _LEVEL_AND_ARCHITECT,
        strong=False,
    ),
    _cue(
        "tech-leadership",
        "tech-lead",
        _any_of(
            [
                r"\btech(nical)? lead(er)?\b",
                r"\bteam lead\b",
                r"\btechnology lead\b",
                r"\bdevelopment lead\b",
            ]
        ),
        _LEVEL_AND_ARCHITECT,
        strong=False,
    ),
    # generic software
    _cue(
        "software-engineering",
        "generic-software",
        _any_of(
            [
                r"software (engineer|developer|development|dev)\b",
                r"\bsde\b",
                r"\bswe\b",
                r"\bback[\s-]?end\b",
                r"member,? (of )?(the )?technical staff",
                r"\b[sp]?mts\b",
                r"founding engineer",
                r"forward[- ]deployed",
                r"\bsystem software\b",
            ]
        ),
        _GENERIC_SOFTWARE,
    ),
    _cue(
        "software-engineering",
        "generic-developer",
        _any_of(
            [
                r"\bdeveloper\b",
                r"\bprogrammer\b",
                r"\bapplications? engineer\b(?=.*\b(software|developer)\b)",
                r"\bsoftware\b",
            ]
        ),
        _GENERIC_SOFTWARE,
        strong=False,
    ),
)

# A non-software discipline, trade, retail or sales word. Beats every weak cue; a strong cue beats
# it ("Software Engineer - Manufacturing Systems", "SAP Consultant - Construction").
_NEGATIVE: dict[str, re.Pattern] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "retail-front-end": _any_of(
            [
                r"\bfront end (team member|associate|clerk|cashier|supervisor|manager|lead|service|checker)\b",
                r"\bcashier\b",
                r"\bgrocery\b",
                r"\bclerk\b",
                r"\bstore (associate|manager)\b",
            ]
        ),
        "civil-building-utilities": _any_word(
            [
                r"civil",
                r"structural",
                r"geotechnical",
                r"roadway",
                r"highway",
                r"bridges?",
                r"transportation (engineer|planner)",
                r"traffic",
                r"water",
                r"wastewater",
                r"stormwater",
                r"piping",
                r"plumbing",
                r"hvac",
                r"mep",
                r"substation",
                r"transmission line",
                r"distribution (engineering )?designer",
                r"building (engineering|design|services)",
                r"construction",
                r"commissioning agent",
                r"surveyor",
                r"environmental",
            ]
        ),
        "mechanical-manufacturing": _any_word(
            [
                r"mechanical",
                r"manufacturing",
                r"process (engineer|engineering|technician|integration)",
                r"project engineering",
                r"flight test",
                r"fluid dynamics",
                r"pi engineer",
                r"production (engineer|technician|supervisor)",
                r"tool(ing)? design",
                r"stamping",
                r"composites?",
                r"nvh",
                r"powertrain",
                r"thermal",
                r"materials? engineer",
                r"metallurg",
                r"welding",
                r"machin(ist|ing)",
                r"cnc",
                r"mold",
                r"casting",
                r"packaging engineer",
                r"industrial engineer",
                r"plant engineer",
                r"maintenance",
                r"reliability technician",
                r"quality (inspector|technician|control)",
                r"supplier quality",
                r"chemical engineer",
                r"cmp",
                r"etch",
                r"lithography",
                r"yield engineer",
                r"design engineer\b(?!.*\b(asic|rtl|ic|chip|circuit|rf|analog|digital|fpga|pcb|hardware|electronics?|software|silicon|soc)\b)",
            ]
        ),
        "electrical-power": _any_word(
            [
                r"electrical engineer",
                r"electrical engineering",
                r"transmission",
                r"power delivery",
                r"controls? systems?",
                r"power systems",
                r"protection (and control )?engineer",
                r"protection commissioning",
                r"controls engineer",
                r"plc",
                r"scada",
                r"instrumentation",
                r"electrician",
                r"high voltage",
                r"switchgear",
                r"energy infrastructure",
            ]
        ),
        "facilities-hospitality": _any_word(
            [
                r"facilit(y|ies)",
                r"hotel",
                r"hyatt",
                r"andaz",
                r"marriott",
                r"hilton",
                r"westin",
                r"sheraton",
                r"ritz",
                r"four seasons",
                r"intercontinental",
                r"holiday inn",
                r"fairmont",
                r"kempinski",
                r"resort",
                r"casino",
                r"pre-opening",
                r"chief engineer",
                r"assistant director of engineering",
                r"engineering (technician|assistant manager|supervisor)",
                r"housekeeping",
                r"janitor",
                r"custodian",
                r"bms",
                r"epms",
                r"data cent(er|re) (project|assistant project|construction|controls|facilities|logistics|mechanical|electrical|capacity)",
            ]
        ),
        "sales-marketing-business": _any_word(
            [
                r"sales (representative|specialist|executive|manager|director|associate)",
                r"account (executive|manager)",
                r"business development",
                r"marketing (manager|specialist|coordinator)",
                r"recruiter",
                r"talent acquisition",
                r"accountant",
                r"bookkeeper",
                r"legal (support|assistant|counsel)",
                r"paralegal",
                r"customer service representative",
                r"call cent(er|re)",
                r"business develop\w*",
                r"product developer",
                r"product development (manager|specialist|coordinator|technologist)",
            ]
        ),
        "healthcare-lab-science": _any_word(
            [
                r"nurse",
                r"rn\b",
                r"physician",
                r"pharmacist",
                r"medical assistant",
                r"clinical research",
                r"lab(oratory)? (technician|scientist|assistant)",
                r"associate scientist",
                r"scientist i\b",
                r"assay",
                r"chemist",
                r"(?<!computational )biologist",
            ]
        ),
        "design-writing-training": _any_of(
            [
                r"\b(graphic|visual|interior|instructional|ux|ui/ux|product|industrial|fashion|motion)\s+designer\b",
                r"\btechnical writer\b",
                r"\bcontent (writer|developer)\b",
                r"\btrainer\b",
                r"\binstructor\b",
                r"\bteacher\b",
                r"\brecipe\b",
                r"data label(l)?ing",
                r"\bannotat",
                r"data contributor",
                r"recording participant",
                r"video data",
                r"ai trainer",
                r"prompt writer",
            ]
        ),
        "physical-security": _any_of(
            [
                r"^(?!.*\b(information|cyber|it|network|data|systems)\b).*\bsecurity (officer|guard|patrol|supervisor|agent)\b",
                r"\b(armed|unarmed|patrol) (officer|guard)\b",
            ]
        ),
        "field-service-trades": _any_of(
            [
                r"\bfield service\b",
                r"\bservice technician\b",
                r"\binstaller\b",
                r"\bcabling (foreman|technician|installer)\b",
                r"\bforeman\b",
                r"\bsite engineer\b",
                r"\bproject engineer\b",
                r"\blogistics\b",
                r"\bwarehouse\b",
                r"\bdriver\b(?! (engineer|developer))",
                r"\bforklift\b",
            ]
        ),
    }.items()
}


# Every family some rule can name; `role_trends` checks the curated map defines each one.
FAMILIES = frozenset(cue.family for cue in _CUES)


class Decision(NamedTuple):
    """One title's verdict. ``family`` is a family name, ``roles.NON_TECH``, or None when no rule
    decided and the caller should fall back to the centroid."""

    family: str | None
    tier: (
        str | None
    )  # "sole-cue" | "precedence" | "generic-software" | "negative" | None
    rule: str | None


_UNDECIDED = Decision(None, None, None)


def classify(title: str | None) -> Decision:
    """The rules' verdict on one title (the module docstring gives the order)."""
    if not title:
        return _UNDECIDED
    matches = []
    for cue in _CUES:
        found = cue.pattern.search(title)
        if found:
            matches.append((cue.rank, found.start(), cue))
    negative = next((name for name, rx in _NEGATIVE.items() if rx.search(title)), None)
    if negative and not any(cue.strong for _, _, cue in matches):
        return Decision(NON_TECH, "negative", negative)
    if not matches:
        return _UNDECIDED
    matches.sort(key=lambda match: (match[0], match[1]))
    best = matches[0][2]
    if best.rank == _GENERIC_SOFTWARE:
        return Decision(best.family, "generic-software", best.name)
    families = {cue.family for _, _, cue in matches}
    return Decision(
        best.family, "sole-cue" if len(families) == 1 else "precedence", best.name
    )


def check_families(family_names: set[str]) -> None:
    """Refuse rules that name a family the curated map lacks, the posture
    :func:`headstart.roles.load_watchlist` takes: a rule for a family that doesn't exist would
    file rows under a name no chart knows."""
    unknown = sorted(FAMILIES - family_names)
    if unknown:
        raise ValueError(
            f"family rules name {unknown}, which config/role_families.json does not define "
            "(ADR-0215)"
        )


def fingerprint() -> str:
    """A short digest over what the rules decide: every cue's family, pattern, precedence and
    strength, and every negative rule. A rule edit changes it with nobody remembering to bump
    anything, and ``trends_epochs`` marks the tick it first runs (ADR-0164). Cue and rule names
    are left out: renaming one changes no verdict."""
    meaning = {
        "cues": [
            (cue.family, cue.pattern.pattern, cue.rank, cue.strong) for cue in _CUES
        ],
        "negative": sorted(rx.pattern for rx in _NEGATIVE.values()),
    }
    return hashlib.sha256(
        json.dumps(meaning, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]

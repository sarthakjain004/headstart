"""
Seed list of known Workday-hosted company career portals.

Each entry is a dict with:
  name     - human-readable company name
  tenant   - Workday tenant subdomain prefix (e.g. "nvidia")
  instance - Workday instance number (e.g. "wd5")  → subdomain = tenant.instance
  jobsite  - the career site path component (e.g. "NVIDIAExternalCareerSite")

API endpoint per company:
  POST https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{jobsite}/jobs

Browse URL:
  https://{tenant}.{instance}.myworkdayjobs.com/en-US/{jobsite}
"""

from __future__ import annotations

SEEDS: list[dict] = [
    # ── Semiconductors & Hardware ─────────────────────────────────────────────
    {"name": "NVIDIA",              "tenant": "nvidia",          "instance": "wd5",  "jobsite": "NVIDIAExternalCareerSite"},
    {"name": "AMD",                 "tenant": "amd",             "instance": "wd5",  "jobsite": "AMD"},
    {"name": "Intel",               "tenant": "intel",           "instance": "wd1",  "jobsite": "External"},
    {"name": "Qualcomm",            "tenant": "qualcomm",        "instance": "wd5",  "jobsite": "careers"},
    {"name": "Broadcom",            "tenant": "broadcom",        "instance": "wd1",  "jobsite": "External_Career_Site"},
    {"name": "Marvell",             "tenant": "marvell",         "instance": "wd1",  "jobsite": "External"},
    {"name": "Applied Materials",   "tenant": "amat",            "instance": "wd1",  "jobsite": "External"},
    {"name": "Lam Research",        "tenant": "lamresearch",     "instance": "wd3",  "jobsite": "LRCareers"},
    {"name": "KLA",                 "tenant": "kla",             "instance": "wd1",  "jobsite": "Search"},
    {"name": "Micron Technology",   "tenant": "micron",          "instance": "wd5",  "jobsite": "External"},
    {"name": "Western Digital",     "tenant": "wdc",             "instance": "wd1",  "jobsite": "External"},
    {"name": "TE Connectivity",     "tenant": "te",              "instance": "wd5",  "jobsite": "TECareers"},
    # ── Enterprise Software ───────────────────────────────────────────────────
    {"name": "Salesforce",          "tenant": "salesforce",      "instance": "wd12", "jobsite": "External_Career_Site"},
    {"name": "Oracle",              "tenant": "oracle",          "instance": "wd1",  "jobsite": "External"},
    {"name": "SAP",                 "tenant": "sap",             "instance": "wd3",  "jobsite": "SAP"},
    {"name": "ServiceNow",          "tenant": "servicenow",      "instance": "wd5",  "jobsite": "External"},
    {"name": "Workday",             "tenant": "wday",            "instance": "wd5",  "jobsite": "Workday"},
    {"name": "Adobe",               "tenant": "adobe",           "instance": "wd5",  "jobsite": "external_university"},
    {"name": "Palo Alto Networks",  "tenant": "paloaltonetworks","instance": "wd3",  "jobsite": "PAN_Career_Site"},
    {"name": "Citrix",              "tenant": "citrix",          "instance": "wd5",  "jobsite": "External"},
    {"name": "Juniper Networks",    "tenant": "juniper",         "instance": "wd5",  "jobsite": "External"},
    {"name": "NetApp",              "tenant": "netapp",          "instance": "wd1",  "jobsite": "External"},
    {"name": "Cisco",               "tenant": "cisco",           "instance": "wd5",  "jobsite": "Cisco_External_Site"},
    {"name": "HP Inc",              "tenant": "hp",              "instance": "wd5",  "jobsite": "ExternalCareerSite"},
    {"name": "Dell Technologies",   "tenant": "dell",            "instance": "wd1",  "jobsite": "External"},
    {"name": "IBM",                 "tenant": "ibm",             "instance": "wd12", "jobsite": "External"},
    # ── Finance & Banking ─────────────────────────────────────────────────────
    {"name": "Goldman Sachs",       "tenant": "goldmansachs",    "instance": "wd1",  "jobsite": "TechCareerOpportunities"},
    {"name": "Morgan Stanley",      "tenant": "morganstanley",   "instance": "wd3",  "jobsite": "Experienced_Jobs"},
    {"name": "BlackRock",           "tenant": "blackrock",       "instance": "wd1",  "jobsite": "Careers"},
    {"name": "Vanguard",            "tenant": "vanguard",        "instance": "wd5",  "jobsite": "Vanguard_Careers"},
    {"name": "State Street",        "tenant": "statestreet",     "instance": "wd1",  "jobsite": "External"},
    {"name": "Citigroup",           "tenant": "citi",            "instance": "wd5",  "jobsite": "External"},
    {"name": "Bank of America",     "tenant": "bofa",            "instance": "wd1",  "jobsite": "Global"},
    {"name": "Wells Fargo",         "tenant": "wellsfargo",      "instance": "wd5",  "jobsite": "WellsFargoJobs"},
    {"name": "TIAA",                "tenant": "tiaa",            "instance": "wd5",  "jobsite": "TIAA_Jobs"},
    {"name": "Northern Trust",      "tenant": "northerntrust",   "instance": "wd1",  "jobsite": "Careers"},
    # ── Tech/Cloud ────────────────────────────────────────────────────────────
    {"name": "Cloudflare",          "tenant": "cloudflare",      "instance": "wd5",  "jobsite": "Cloudflare"},
    {"name": "Datadog",             "tenant": "datadog",         "instance": "wd5",  "jobsite": "External"},
    {"name": "MongoDB",             "tenant": "mongodb",         "instance": "wd5",  "jobsite": "External"},
    {"name": "Elastic",             "tenant": "elastic",         "instance": "wd5",  "jobsite": "External"},
    {"name": "Splunk",              "tenant": "splunk",          "instance": "wd5",  "jobsite": "External"},
    {"name": "New Relic",           "tenant": "newrelic",        "instance": "wd5",  "jobsite": "Jobs"},
    {"name": "Dynatrace",           "tenant": "dynatrace",       "instance": "wd5",  "jobsite": "External"},
    {"name": "CrowdStrike",         "tenant": "crowdstrike",     "instance": "wd5",  "jobsite": "CrowdStrikeCareers"},
    {"name": "OKTA",                "tenant": "okta",            "instance": "wd5",  "jobsite": "External"},
    {"name": "Zscaler",             "tenant": "zscaler",         "instance": "wd5",  "jobsite": "External"},
    {"name": "Fortinet",            "tenant": "fortinet",        "instance": "wd5",  "jobsite": "External"},
    {"name": "F5",                  "tenant": "f5",              "instance": "wd5",  "jobsite": "f5jobs"},
    {"name": "Zoom",                "tenant": "zoom",            "instance": "wd5",  "jobsite": "External"},
    # ── Gaming & Media ────────────────────────────────────────────────────────
    {"name": "EA",                  "tenant": "ea",              "instance": "wd3",  "jobsite": "EA"},
    {"name": "Warner Bros",         "tenant": "warnermediajobs", "instance": "wd5",  "jobsite": "WarnerBrosTechJobs"},
    # ── Aerospace / Defense ───────────────────────────────────────────────────
    {"name": "Boeing",              "tenant": "boeing",          "instance": "wd1",  "jobsite": "EXTERNAL_CAREERS"},
    {"name": "Raytheon",            "tenant": "rtx",             "instance": "wd5",  "jobsite": "RTX_External_Career_Site"},
    {"name": "Lockheed Martin",     "tenant": "lmco",            "instance": "wd1",  "jobsite": "External"},
    {"name": "Northrop Grumman",    "tenant": "northropgrumman", "instance": "wd1",  "jobsite": "NGCCareers"},
    {"name": "L3Harris",            "tenant": "l3harris",        "instance": "wd5",  "jobsite": "L3Harris"},
    # ── Auto / Mobility ───────────────────────────────────────────────────────
    {"name": "GM",                  "tenant": "generalmotors",   "instance": "wd5",  "jobsite": "careers-home"},
    {"name": "Ford",                "tenant": "ford",            "instance": "wd5",  "jobsite": "Ford_Career_Page"},
    {"name": "Rivian",              "tenant": "rivian",          "instance": "wd5",  "jobsite": "Rivian"},
    {"name": "Lucid Motors",        "tenant": "lucidmotors",     "instance": "wd5",  "jobsite": "LucidMotors"},
]


def fetch() -> list[dict]:
    """Return the seed list of Workday company configs."""
    return list(SEEDS)

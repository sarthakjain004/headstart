"""
Seed list of known Greenhouse-hosted job boards.

These are organisations confirmed to use boards.greenhouse.io/{slug}.
They seed the discovery pipeline; GitHub / HN / URLScan will find many more.
"""

SEEDS: list[str] = [
    # ── Top-tier tech ──────────────────────────────────────────────────────────
    "stripe", "reddit", "discord", "twitch", "coinbase", "robinhood",
    "brex", "duolingo", "asana", "plaid", "chime", "benchling",
    "scale", "lob", "postman", "snyk", "gem", "launchdarkly",
    "sendbird", "contentful", "mparticle", "iterable", "attentive",
    "klaviyo", "amplitude", "mixpanel", "braze", "segment",
    "dbtlabs", "fivetran", "airbyte", "starburst", "imply",
    "clickhouse", "temporal", "buf", "pagerduty", "victorops",
    "opsgenie",
    # ── Infrastructure / cloud ────────────────────────────────────────────────
    "render", "pusher", "fastly", "cloudflare", "algolia",
    "elastic", "confluent", "cockroachdb", "yugabyte", "timescale",
    "neon", "planetscale", "supabase",
    # ── Security ──────────────────────────────────────────────────────────────
    "vanta", "drata", "lacework", "orca", "semgrep", "snyk",
    "detectify", "stackhawk",
    # ── ML / AI ───────────────────────────────────────────────────────────────
    "huggingface", "weights-biases", "comet-ml", "arize",
    "labelbox", "scale",
    # ── Finance / fintech ─────────────────────────────────────────────────────
    "marqeta", "adyen", "checkout-com", "galileo", "tabapay",
    "bluesnap", "payoneer", "rapyd",
    # ── Enterprise SaaS ───────────────────────────────────────────────────────
    "zendesk", "freshworks", "intercom", "drift", "outreach",
    "salesloft", "groove", "gong", "chorus", "clari",
    "highspot", "showpad",
    # ── Developer tools ───────────────────────────────────────────────────────
    "jetbrains", "atlassian", "airtable", "notion", "coda",
    "retool", "appsmith", "tooljet",
    # ── Health / bio ──────────────────────────────────────────────────────────
    "modernhealth", "headway", "cerebral", "lyra", "spring",
    "hims-hers", "nurx", "ro", "sesame",
    # ── E-commerce / marketplace ──────────────────────────────────────────────
    "faire", "order", "convictional", "vtex",
    # ── Autonomous / robotics ─────────────────────────────────────────────────
    "gatik", "nuro", "aurora", "kodiak", "torc",
    # ── Climate / energy ──────────────────────────────────────────────────────
    "form-energy", "antora", "verdagy",
]


def fetch() -> set[str]:
    return set(SEEDS)

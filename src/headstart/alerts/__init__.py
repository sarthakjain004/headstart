"""Job alerts and the signed-in user's stored state.

Email alerts (ADR-0035) and their Telegram sibling (ADR-0038) started this package; it now
also holds what the signed-in UI stores per user — Profile (ADR-0041), SavedSet (ADR-0042,
ADR-0043) and SavedJob (ADR-0044) — because all of it is one HF dataset behind one client.

Deliberately empty: `deploy-space.yml` copies this package into the Space, where only
`store`, `identity` and `access` are imported. Re-exporting members here would make that
import pull in `digest` and `mail` — and their xlsxwriter/Resend dependencies — into an
image that has neither.
"""

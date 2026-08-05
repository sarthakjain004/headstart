"""Email job alerts (ADR-0035).

Deliberately empty: `deploy-space.yml` copies this package into the Space, where only
`store`, `identity` and `access` are imported. Re-exporting members here would make that
import pull in `digest` and `mail` — and their xlsxwriter/Resend dependencies — into an
image that has neither.
"""

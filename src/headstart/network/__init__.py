"""How a request leaves the machine (ADR-0232): the pooled HTTP client, its browser twin, the Fetcher
seam both sit behind, the spare egress a walled shard falls back to, and the per-width record of how
wide its requests fanned out.
"""

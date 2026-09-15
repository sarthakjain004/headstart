# HF Docker Space for the HeadStart semantic search (ADR-0020).
# The embedding model is baked into the image (fast cold starts); the LanceDB index is pulled
# from the private HF dataset at container start, so a Space restart picks up a fresh index
# without an image rebuild.
FROM python:3.12-slim

WORKDIR /app
ENV HF_HOME=/app/hf-cache
# Xet off, matching every other HF download in this repo (Xet has died mid-transfer here
# before, silently and without a traceback). The Space was the one consumer that never got the
# flag, because it had never been rebuilt onto an image carrying hf-xet — the 2026-09-09
# rebuild pulled hf-xet 1.6.0 in for the first time. Baked in rather than set as a Space
# variable so it is visible in the repo and cannot silently fail to propagate.
ENV HF_HUB_DISABLE_XET=1

# SSH client + autossh for the llm-router tunnel (ADR-0032). The router is reachable only
# through it; the tunnel is optional at runtime — see start.sh, which must never let a
# tunnel failure stop the app from booting.
RUN apt-get update && apt-get install -y --no-install-recommends \
      openssh-client autossh && rm -rf /var/lib/apt/lists/*

# CPU-only torch first — the default Linux wheel drags the multi-GB CUDA stack.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the encoder into the image.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True)"

# `headstart` (ADR-0153) is a real Python package here, not a hand-curated file list: this
# COPY brings in the WHOLE src/headstart tree (deploy-space.yml stages it beside this
# Dockerfile first, since the Space's own repo — not the monorepo — is the build context).
# app.py imports it exactly like `scripts/ui/serve.py` does (`from headstart import search,
# facets, ...`), and the UI's templates/static, previously synced as their own COPY lines,
# ride inside it at headstart/ui/ — app.py locates them via `Path(headstart.__file__)`.
# `config/` (fx_rates.json, role_families.json, role_watchlist.json) is the same story: one
# directory copy instead of three individually-named files that had to be kept in step with
# the workflow's sync list. Without role_families.json every trend series renders as its slug
# (ADR-0040); without role_watchlist.json `_watch_meta` returns {} and `watch_parents` is
# empty, so the by-role drill stays hidden on every family (ADR-0051) — the same bug as #115,
# one file later. `fx.py` finds fx_rates.json the same way (ADR-0117): both are now structural
# (the file must exist under config/), not enumerated.
COPY app.py start.sh ./
COPY headstart ./headstart
COPY config ./config
RUN chmod +x start.sh
EXPOSE 7860
CMD ["./start.sh"]

# HF Docker Space for the HeadStart semantic search (ADR-0020).
# The embedding model is baked into the image (fast cold starts); the LanceDB index is pulled
# from the private HF dataset at container start, so a Space restart picks up a fresh index
# without an image rebuild.
FROM python:3.12-slim

WORKDIR /app
ENV HF_HOME=/app/hf-cache

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

# geo.py, llm_router.py, resume_query.py and the alerts/ package are not in the repo's
# deploy/hf-space/ — deploy-space.yml copies them in from src/headstart/ (single source of
# truth, where their tests live) before pushing to the Space.
COPY app.py geo.py llm_router.py resume_query.py search.py start.sh .
COPY alerts ./alerts
# Synced from src/headstart/ui/ by deploy-space.yml, like the modules above.
COPY templates ./templates
COPY static ./static
RUN chmod +x start.sh
EXPOSE 7860
CMD ["./start.sh"]

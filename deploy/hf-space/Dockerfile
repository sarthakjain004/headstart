# HF Docker Space for the HeadStart semantic search (ADR-0020).
# The embedding model is baked into the image (fast cold starts); the LanceDB index is pulled
# from the private HF dataset at container start, so a Space restart picks up a fresh index
# without an image rebuild.
FROM python:3.12-slim

WORKDIR /app
ENV HF_HOME=/app/hf-cache

# CPU-only torch first — the default Linux wheel drags the multi-GB CUDA stack.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bake the encoder into the image.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True)"

# geo.py is not in the repo's deploy/hf-space/ — deploy-space.yml copies it in from
# src/headstart/geo.py (single source of truth) before pushing to the Space.
COPY app.py geo.py .
EXPOSE 7860
CMD ["python", "app.py"]

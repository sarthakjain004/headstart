# The Space went down for ~1h: an unpinned stack, and a fix nobody can prove

**Date:** 2026-09-09 → 2026-09-10 · **Status:** recovered; **cause not conclusively attributed**
· **Not caused by ADR-0120's code** — surfaced by the rebuild that change triggered.

## Summary

Merging #394 (the trends ledger's move to Parquet) added one line to
`deploy/hf-space/requirements.txt`. That triggered `deploy-space.yml`, which rebuilt the image.
The Space then failed to start — five times — and served a bare 503 for about an hour.

It recovered after **two changes landed at once**, and the evidence cannot separate them. See
[Attribution](#attribution-why-the-pin-is-not-proven).

## The failure

```text
Fetching 150 files:  13%|█▎        | 19/150
huggingface_hub.errors.FileMetadataError: Response from
  https://huggingface.co/datasets/imPoseidon/headstart-index/resolve/344432a4…/
  data/lancedb/jobs.lance/_deletions/10-28-6342666340804300008.arrow
  is missing the 'X-Repo-Commit' header, so it does not seem to be served by a Hugging Face
  Hub endpoint.
The above exception was the direct cause of the following exception:
  File "/app/app.py", line 50, in <module>
    snapshot_download(
…
huggingface_hub.errors.LocalEntryNotFoundError: …
```

Same file, same revision, every time. The progress counter varied (18–23 of 150); the named file
never did. Deterministic, not a transient — which is why a retry was deliberately *not* shipped as
the fix: it would only have failed more slowly.

**`LocalEntryNotFoundError` does not mean "the file is absent".** It subclasses
`EntryNotFoundError` but means *could not reach or resolve* — the inversion this repo's notes
already record. Read as "file missing", it sends you hunting for a file that is demonstrably
present.

## What made it possible: the stack was never pinned

`deploy/hf-space/requirements.txt` was fully unpinned, so **Docker's layer cache was the only
thing holding the stack still**. The versions actually running were whatever pip resolved the last
time that file changed — 2026-08-06, commit `2eda202`. One added line invalidated that layer and
re-resolved **a month of drift in a single rebuild**:

| package | was (2026-08-06) | rebuilt to |
|---|---|---|
| `huggingface_hub` | 1.26.1 | **1.30.0** |
| `lancedb` | 0.36.0 | **0.38.0** |
| `sentence-transformers` | 5.7.0 | **6.0.1** |
| `pyarrow` | 25.0.0 | 25.0.1 |
| `google-auth` | 2.56.3 | 2.57.1 |

The traceback is version-shaped — it dies in `_raise_on_head_call_error`
(`file_download.py:1918`), reached from `_hf_hub_download_to_local_dir` (`:1381`), a
`huggingface_hub` 1.x path.

An unpinned production image is not "current". It is **frozen until the next unrelated edit, then
arbitrary** — and the edit that unfreezes it is unrelated to the breakage it causes, which is what
makes this so hard to diagnose. `pyproject.toml` already pins `ruff` for the same reason. #401
pinned the Space to the reconstructed pre-rebuild versions.

## Attribution: why the pin is not proven

Between the last failure and recovery, **two things changed**:

1. The pin landed (#401).
2. **The dataset HEAD moved**: `344432a4…` → `b8f596d8…`. A pipeline merge rewrote the repo.

The offending `_deletions` object is **still present** at the new HEAD, so it was not removed — but
`snapshot_download` resolves against the *current* revision, so the Space now fetches that object
at a different commit. Either change could have fixed it.

**Recorded as: recovered after two simultaneous changes; not attributable.** The pin stays because
pinning an unpinned production stack is correct on its own merits and this outage proved the cost
of leaving it unpinned — but **it is unvalidated as a fix for this failure**, and the record should
not imply otherwise.

## Seven hypotheses eliminated by measurement

Recording what it *wasn't* is worth as much as the fix, because four of these were confidently
held at some point — two by me, two by the reviewer — and each would have sent a fix somewhere
useless.

| # | Hypothesis | How it died |
|---|---|---|
| 1 | The ADR-0120 Parquet change | Failing path is `data/lancedb/*` — predates it, untouched by it |
| 2 | Dataset squash invalidated the pinned revision | Dataset HEAD **was** `344432a4…`, and the file **was** present at HEAD |
| 3 | Torn table from the in-flight pipeline run | That run had not reached its `merge` job; no lancedb uploaded |
| 4 | Dependency *import* break from the rebuild | Traceback is at `app.py:50` — every import had already succeeded |
| 5 | llm-router tunnel proxying Hub traffic | `start.sh` opens a plain `-L 4000:127.0.0.1:4000`; sets no proxy vars |
| 6 | Xet | Flag set as a Space variable, **verified present**, restart at a recorded 14:57:12Z → identical failure at 14:57:42Z. Also: `hf-xet` ships with `huggingface_hub` 1.26.1 too, so the pre-rebuild image had it as well |
| 7 | Stale/corrupt local cache surviving restarts | `storage: None` — the container is ephemeral; every restart starts clean |

The file itself fetched **11/11** from outside the container, including 3/3 on the Space's exact
`huggingface_hub==1.30.0` with `hf-xet 1.6.0` installed. Whatever it was, it was specific to the
container's egress *and* to the newly-resolved stack.

### Two reasoning errors worth keeping

- **"It reached `app.py:50`, so imports succeeded, so it isn't the dependency re-resolution."**
  The premise is right and the conclusion does not follow: reaching line 50 rules out *import*
  failures, not *behavioural* changes in a newly-resolved library. Only reproducing on the Space's
  exact `huggingface_hub==1.30.0` actually closed that off.
- **Testing a container hypothesis on a laptop.** The "3/3 with Xet on" result was run from a
  machine whose egress and installed state differ from the container's. The right control is the
  container — which is why the flag was then set *in the Space* and restarted with a recorded
  timestamp, so the boot could be attributed unambiguously.

## Open follow-ups

- **A retry around the startup download.** Not this bug's fix, but a real fragility: one
  unreachable file out of 150 takes the whole product down with no self-recovery. Cheap, because
  the download resumes — measured against this dataset: cold 13.80s, warm 0.34s (40×), and 1.58s
  with five files missing.
- **Failing soft on the index pull**, *conditionally*. A search product that comes up without its
  index must refuse index-backed requests explicitly and never return empty results — a wrong
  answer is worse than an honest outage. It also needs a replacement health signal: HF's
  `RUNTIME_ERROR` stage *is* today's alarm, and a "healthy but degraded" app destroys it unless
  something else watches. Without that, this trades a loud failure for a silent one — the same
  pattern as ADR-0053's undrained scope exclusion and the blind spare-egress "rescued" metric.
- **Transitive drift is still unpinned.** #401 pins the direct dependencies only;
  `transformers` (5.14.1 → 5.16.1), `tokenizers` and `numpy` still float. A lockfile would be the
  complete answer.

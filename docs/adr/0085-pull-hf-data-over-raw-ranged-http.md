# ADR-0085: Pull HF data over raw, ranged HTTP — not `snapshot_download`

**Status:** Accepted · **Date:** 2026-08-25 · **Extends ADR-0036 (disable Xet); follows ADR-0002's per-thread session rule**

## Context

ADR-0036 already made one call about this transport: use the plain resolve/CDN path rather than
Xet, because both are rate-limited and only the plain one *recovers*. It scoped that to setting
`HF_HUB_DISABLE_XET=1` and kept `huggingface_hub` as the client.

Pulling `data/lancedb` on 2026-08-25 (4,222 files, 1,888 MB, of which a single 1,023 MB `.lance`
data file) showed the client itself is the remaining problem. It failed four separate ways:

1. **Silent death under Xet** — twice, at the same 987 files, no traceback, process simply gone.
   This is ADR-0036's failure recurring, because that ADR's env var is set on the *workflows*;
   a laptop pull picks up Xet automatically whenever `hf_xet` is installed.
2. **An internal retry loop that never raises.** `HF_HUB_DOWNLOAD_TIMEOUT` defaults to 10s and is
   a **read** timeout. A chunk that missed the window logged "Trying to resume" and looped inside
   the library, so a per-file retry wrapped around it never fired. Raising the timeout to 300s
   made the hangs *longer*, not rarer.
3. **No resume on the file that needed it.** The 1,023 MB file restarted from byte zero on every
   CDN drop, each time under a fresh `.incomplete` name. Three partials of one blob accumulated
   (314.6 + 167.8 + 234.1 MB) and none of them resumed.
4. **A wedge with no work in flight** — the process alive with **zero sockets open and zero locks
   held**, while a plain `requests.get` of one of the very files it was fetching returned 200 in
   0.28s.

Only the resolve endpoint was ever healthy.

## Decision

**`scripts/fetch/pull_lancedb.py` fetches over raw HTTP, and large files as concurrent ranged
chunks.** Four properties, each answering a specific failure above.

### 1. Concurrent ranged chunks, because the bottleneck is per-flow

Measured mid-transfer, same link, same file: one long-lived stream had decayed to **0.16 MB/s**
while four concurrent ranged GETs aggregated **2.17 MB/s** — ~13x. The sequential run's first
50 MB arrived at 6.36 MB/s and decayed monotonically from there, which is the same observation
from the other side. Short flows fast while one long flow starves is TCP congestion-window
behaviour and per-flow shaping, not a saturated last mile.

The operational consequence is the part worth remembering: **when this is slow, add flows
(`--workers`), never timeout.** Confusing the two is what turned a slow transfer into a wedged
one. After the switch, the last 337 MB landed in ~90s against the ~46 min the single stream was
projecting.

Aggregate gain is sub-linear in connection count, so the default is 6–8, not 32.

### 2. Absolute chunk boundaries, so cancellation is free

Chunk *i* always covers the same byte range regardless of what has landed, so a chunk file's own
length is sufficient to say how much of it is done. There is no manifest to write, corrupt, or
let drift out of step with the disk — which is precisely the bookkeeping `huggingface_hub` got
wrong in failure 3.

Because the boundaries are absolute, **a resume with a different `--chunk-mb` would carve the
same bytes at different offsets and silently assemble a corrupt file.** A guard refuses that
rather than trusting the operator to remember.

### 3. `.tmp`-then-rename, because a short file is worse than a missing one

Each run decides what to fetch with `exists()`. A run killed mid-write must therefore never leave
a short file at the real path — it would be counted as landed and skipped forever, corrupting the
table with a truncated manifest that surfaces much later as an unrelated-looking decode error.

The same reasoning gives large files a size check before the rename.

### 4. One recovery path that is not obvious and must not be deleted

Concatenation appends each chunk to `.part` and deletes it, so an interrupt *there* leaves
progress in two shapes at once: a `.part` holding chunks 0..k, plus chunk files for k+1..n.
`_recover_partial_concat` carves the `.part` back into chunks, restoring one shape at no refetch
cost. Verified by simulating that interrupt on a 4-chunk file and rebuilding it byte-exact.

It reads like dead code — a code review called it exactly that — so it is named and documented
for the interrupt it actually handles, not for the migration it originally performed.

## Consequences

`snapshot_download` remains fine for the small slices CLAUDE.md already recommends
(`data/state/*` is ~1 MB); this is for the multi-GB ones. The cheap-read guidance there is
unchanged and still the first thing to try.

Sessions are per-thread (`threading.local`), following ADR-0002. Its stated reason is
curl_cffi-specific, but `requests.Session` is likewise not documented thread-safe and every fetch
here runs under a `ThreadPoolExecutor`, so the repo's shape is the safe default rather than
relying on urllib3's pool happening to tolerate the sharing.

The cost is a tool to maintain against an API `huggingface_hub` also targets. That is accepted
because the surface used is small and stable — `repo_info` for the file list and sizes, and
`hf_hub_url` plus a bearer token for the fetch — and because the alternative was measured, not
assumed, to fail.

# Description drift measurements

`scripts/eval/measure_content_drift.py` pins the freshest HF description-store revision,
draws a seeded sample from Scrapable Boards, then compares their live descriptions.
It defaults to three Lever Boards and ten held Jobs per Board; reference downloads
are bounded at 64 MiB. All private reference text and per-Job results stay under the
gitignored `artifacts/<UTC timestamp>/` directory. Output streams per Board.

Run: `.venv/bin/python -u scripts/eval/measure_content_drift.py`.
The report distinguishes unchanged, different, not returned, and no current description.
Not returned does not establish closure; errors and truncations are reported separately.
Differences can come from parser changes, not only employer edits. This sample does
not measure every ATS, price detail-pass refresh, or justify a refresh cadence by itself.

The user retained ADR-0021's deferred refresh policy. Measurements inform a later
decision; this tool does not change held descriptions, vectors, or production state.

# Live 429 egress controls

Measured 2026-09-13 from the development host with direct traffic and the connected WARP SOCKS5H
proxy (`socks5h://127.0.0.1:40000`). These are controls for the 429 rotation change, not a claim
that a low-volume sample can reproduce a production threshold.

| ATS / surface | Board(s) | direct | WARP | result |
| --- | --- | ---: | ---: | --- |
| Eightfold detail | `nvidia.eightfold.ai` | 100 × 200 | 100 × 200 | No 429 reproduced; both arms parsed the endpoint response. |
| Oracle detail | `ejwl.fa.us2.oraclecloud.com` (the production-affected pod) | 50 × 200 | 50 × 200 | No 429 reproduced at 16-wide; both arms reached detail payloads. |
| SuccessFactors detail | `careers.te.com`, `jobs.l3harris.com`, `careers.bureauveritas.com` | 9 × 200 | 9 × 200 | No 429 reproduced; all pages parsed. |
| Taleo BE detail | one live ledger Board / posting | 1 × 200 | 1 × 200 | No 429 reproduced. |
| Taleo Enterprise detail | D.R. Horton job `268694` | 1 × 200 | 1 × 200 | No 429 reproduced. |

The production reason for the change remains run `34767229592`: Eightfold emitted 5,497 HTTP 429
detail events across 34 Boards and Oracle emitted 1,357 across two pods. The controls establish that
both routes are live and usable now; they do **not** establish that every 429 is IP-scoped. The
implementation therefore keeps the existing retry ladder and only adds 429 to the configured
spare-egress wall set for the affected/requested ATSes.

The Taleo Business Edition alias request is also wired through `_egress()`, so its new opt-in is not
inert on the dedup/liveness path.

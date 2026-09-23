# Tech filter version 4: spellings the gate could not see, and the trades it let in

2026-09-23. `TECH_FILTER_VERSION` 3 → 4. Decision record: [ADR-0179](../adr/0179-the-tech-filter-stays-english-and-its-trade-vetoes-stand-down-for-infrastructure.md).

## Why

A critique of version 3 found that ADR-0017's near-total recall had never been measured. Its only
evidence was 0 false negatives in a sample of 100, whose 95% upper bound is 3%. The dropped pile
is about 5x the kept pile, so that bound allows recall as low as about 87%.

The misses fell into two groups:
- **English spellings the regexes were blind to:**
  - `_` used as a separator, because `_` is a word character to `\b`.
  - A level glued to the acronym: "SDE3".
  - Plurals: "PHP Developers".
  - Abbreviations: "Software Engr II", "Java Lead".
- **Whole role families outside the strong list:** DevSecOps, "IT Project Manager", cyber, IAM
  and quant roles.

On the precision side, the bare "…engineer" token was admitting the construction trades in bulk.

Non-English titles also fell through. That gap is deliberately left open: the gate stays English
(ADR-0179).

## What changed

- **Recall (strong list):** the spellings and families above. Where the bare form is ambiguous,
  each is tied to a discipline word: `eng`/`dev` never count alone, because "ENG/SPA" is a
  language pair and "Business Dev" is sales. `cyber` needs a practitioner's role word, because on
  its own it also names the market a sales role serves. "AI Specialist" is excluded: in this data
  it is mostly the crowdwork labelling role.
- **Setting vs. discipline:** `hardware`, `manufacturing` and `mining` no longer veto a title
  that also names software work ("Hardware Test and Validation Engineer", "MES Engineer",
  "Process Mining Developer").
- **Trade vetoes (title only):**
  - The vetoed titles are site, MEP, QA/QC and highway engineers, and business developers.
  - Each stands down when the title or department names IT, network, telecom, data-center,
    SCADA, software, automation or test work (`_INFRA_CONTEXT`).
- **`_STRONG_NOT`:**
  - It sets aside a phrase that trips a strong signal while naming another trade: the retail
    "Front End Manager", "CNC Programmer", a law "JD/LLM", and "Mechanical Engineering Manager".
  - Joint titles are exempt ("Firmware & Electrical Engineering Manager").
  - A title with nothing left is refused without department rescue.

## Measured

Three corpora, old (`origin/main`) vs. new verdict on every row:

| corpus | rows | kept before | kept after | in | out |
|---|---|---|---|---|---|
| served LanceDB table (v654) | 514,163 | 513,389 | 510,863 | +1 | −2,527 |
| pre-filter snapshot (`data/jobs/*.jsonl`, July) | 332,383 | 68,600 | 69,571 | +1,163 | −192 |
| Indeed harvest (secondary, not tuned on) | 679,687 | 160,294 | 162,503 | +4,409 | −2,200 |

**Every served row that flips to dropped was read by hand: 1,556 unique titles.** No software or
IT role survives in that set. By family:
- business developer 496 rows;
- site engineer 479;
- highway 451;
- the discipline "…Engineering Manager" titles 381;
- CNC programmer 260;
- MEP 177;
- QA/QC 129;
- the retail front end 123;
- JD/LLM 1;
- 30 others, mostly underscore-separated titles whose now-visible words say what they are
  ("Registered Nurse_Montana", "Admin Assistant_Sacramento CA").

Rows are counted in the first family whose words they match, in the order above.

Reading the losses found six real tech jobs, and each got a fix before these figures were taken:
- Oracle's `DC Ops` "Site Engineer I/II/III" and "Site Engineer - IP Network" / "-SCADA";
- "Software QA/QC Engineer" and a "QA/QC Engineer" in `IT`;
- "Firmware & Electrical Engineering Manager";
- "IT Site Engineer - Japan";
- "IT Security Auditor";
- "Service Desk Analyst".

The served table can only show losses, because it holds only rows the old gate kept. The gains
are the pre-filter snapshot's +1,163. The largest are "Senior Software Engg - Systems" (62) and
"Delv Senior Software Eng" (51), followed by "IT Project Manager", "Java Dev", "Quant
Researcher" and "Citrix Administrator".

## The labelled evaluation set

`tests/fixtures/tech_filter_eval.tsv` holds 971 English titles with their departments. They come
from our own data:
- 400 from the served table;
- 300 that version 3 dropped from the snapshot;
- 300 whose verdict version 4 changed.

Two labellers labelled every row blind to the gate. They agreed on 97.3%, and the 27
disagreements were settled by hand: plain product managers, hardware validation and
customer-support engineers are `ambiguous` and unscored. 29 non-English rows were removed.

| | recall (345 tech) | kept non-tech (540) |
|---|---|---|
| version 3 | 265 (76.8%) | 282 |
| version 4 | 344 (99.7%) | 86 |

The sample is stratified toward dropped and changed rows, so these are regression gates, not
population rates. `test_the_labelled_set_keeps_every_tech_title_but_the_known_misses` fails on
any new miss. It also fails when a known miss gets fixed, so the list only shrinks on purpose.
Both tests fail against version 3, as they should.

The remaining false positives are the recall-biased "…engineer" creep: field service, process,
quality and commissioning engineers. That is the trade ADR-0017 accepts.

## Not done

- **Non-English vocabulary:** left out by decision (ADR-0179).
- **Crowdwork "AI Trainer" gig titles:** unchanged. Stripping them would have dropped 59 pre-filter
  snapshot rows, some of them coding gigs, and whether they belong is a separate decision.
- **A description fallback for rejected titles:** it would recover titles with thin wording, but
  ADR-0166's listing-time gate means those descriptions are never fetched on six ATSes.

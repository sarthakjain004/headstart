# ADR-0354: An eval task may accept, but never require, the path its tool steers away from

**Status:** accepted · **Date:** 2026-09-29 · **Amends:**
[ADR-0334](0334-connecting-is-counted-apart-and-the-eval-judges-truth-not-one-path.md) (the eval
judges truth, not one path) · **Relates to:**
[ADR-0342](0342-the-sponsorship-eval-judges-apart-from-the-spaces-rules.md)

## Context

The round-4 critique of the Space MCP server found the eval stale again. All five judged failures
were verifier faults (P1-4). The worst was t27, "jobs in the Netherlands whose descriptions mention
relocation". It required `keyword: "relocation"`. Both runs used `work_authorization:
offers_relocation`, and both failed. That is the path search_jobs' own description recommends: "For
visa sponsorship or relocation use `work_authorization`, never `keyword`". Round 3's t13 had the
same fault (SP7, ADR-0342). Each time, the fix came one hosted eval after the tool changed.

## Decision

A task may accept the path a tool's description steers away from, as one of its `any_of` paths. It
may not require it.

`tests/test_space_mcp_eval.py` enforces this in CI. It reads every steer a tool's description
states in the form "For X use `a`, never `b`". Then it walks every task's verifier tree and fails
when a task passes only answers that sent `b` on topic X. A task fails the check when:

- a `tool_args` check requires `b` with a value naming the topic;
- an `all_of` holds such a check; or
- every path of an `any_of` is such a check.

The test also fails if it finds no steer, so a reworded description cannot make it pass by
checking nothing. t27 is now an `any_of`: `work_authorization: offers_relocation` first, then the
description keyword, each with `country: NL` and `page >= 2`.

Three other calls from the same round are recorded here, since none needs an ADR of its own:

- **Unread employment types stay unread.** The critique listed "SG", "BU", "Employee",
  "Professional" and "OTHER". Measured on the served table (499,841 rows, 2026-09-29), none states
  an employment type:
  - "SG" (106 rows) and "BU" (85) are Capital One codes on Radancy, on staff and business roles.
  - "Employee" (1,152) and "Professional" (200) do not say the hours.
  - "OTHER" (4,870, iCIMS) holds 330 intern titles.

  Instead, a `search_jobs` row now says what the filter did with the value: "(full-time, by
  default)" for a value no rule reads (ADR-0341), and "(internship, from the title)" where the
  title gave the reading (ADR-0340).
- **Text addressed to AI tools.** Each pattern was tightened until a read of every hit found none
  addressed to a person. The loose first draft took phrases like "If you are an AI engineer" and
  "instructions to AI coding tools". Over 586,976 stored descriptions the final patterns flag 54,
  and every one is addressed to an AI tool. `get_job` adds one line above such a description and
  quotes the description unchanged.
- **An unknown country.** The refusal no longer lists every code. It names the closest listed
  country ("Germny" is DE) where one is close, and points to `location` for a country the filter
  does not list.

## Consequences

- An agent that follows a tool's own guidance can no longer fail a task for it.
- A new steer ("For X use `a`, never `b`") is picked up without editing the test.
- A steer worded any other way is not enforced. The test asserts the list of steers it found, so a
  steer that disappears breaks CI instead of passing silently.

## Alternatives

- **Document the rule in `docs/agents/space-mcp-server.md` only.** Rejected: ADR-0342 fixed t13 by
  hand, and t27 went stale the same way a round later. A written rule did not stop that.
- **Drop the keyword path from t27.** Rejected: the prompt asks for descriptions that *mention*
  relocation. The description keyword answers that literally, and the Space applies it under
  `strict=1`.

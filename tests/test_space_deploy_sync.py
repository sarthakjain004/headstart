"""Every flat file deploy-space.yml syncs into the Space must also be COPYed into the image.

This pairing has broken twice. #115 fixed it for `role_families.json` — the workflow synced it
to the Space repo but the Dockerfile never copied it in, so `_family_labels` read a missing path
and every trend series rendered as its slug. #128 hit the identical bug one file later with
`role_watchlist.json`: `_watch_meta` returned `{}`, `watch_parents` came back empty, and the
by-role drill stayed hidden on every family.

Both failures are silent — the workflow is green, the image builds, the Space boots, and only a
feature quietly does nothing. The two files are edited in different places by different changes,
so nothing but this check keeps them in step.

Stdlib-only and file-based, so it runs in CI's quality job with no Docker and no YAML dependency.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "deploy-space.yml"
DOCKERFILE = REPO / "deploy" / "hf-space" / "Dockerfile"

# `cp <src> deploy/hf-space/<dest>` — flat files only. `\S+` for the source eats a `-r` flag
# before it reaches `deploy/hf-space/`, so the synced DIRECTORIES (alerts/, templates/, static/)
# never match here and are not checked; they ride their own `COPY <dir> ./<dir>` lines, and
# their failure mode is louder — a missing package breaks the import, not one silent feature.
_SYNCED_FILE = re.compile(r"^\s*cp\s+\S+\s+deploy/hf-space/([\w.-]+)\s*$", re.M)


def _copied_into_image() -> set[str]:
    """Every path named on a `COPY` line in the Dockerfile."""
    copied: set[str] = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY "):
            continue
        # Drop the `COPY` verb and the destination (last token, `.` or `./name`).
        copied.update(line.split()[1:-1])
    return copied


def test_every_synced_file_is_copied_into_the_image():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    synced = set(_SYNCED_FILE.findall(workflow))
    assert synced, (
        "no `cp … deploy/hf-space/<file>` lines found — did the sync step move?"
    )

    missing = sorted(synced - _copied_into_image())
    assert not missing, (
        f"deploy-space.yml syncs {missing} into the Space but the Dockerfile never COPYs "
        "them into the image — app.py reads these from beside itself, so they would be "
        "missing at runtime and the feature would silently do nothing"
    )

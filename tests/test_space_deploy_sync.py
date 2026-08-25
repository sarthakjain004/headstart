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
_SYNCED_FILE = re.compile(r"^\s*cp\s+\S+\s+deploy/hf-space/([\w.-]+)\s*$", re.MULTILINE)


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


# `from headstart…` — the package form, which does not exist inside the Space image.
_PACKAGE_IMPORT = re.compile(r"^\s*(?:from|import)\s+headstart\b.*$", re.MULTILINE)


def test_every_package_import_in_a_synced_module_has_a_flat_fallback():
    """A module synced into the Space must import both ways, because the image has no package.

    deploy-space.yml lays each module down *flat* beside `app.py`, so `from headstart import x`
    raises `ModuleNotFoundError` there and nowhere else. That is invisible to the rest of this
    suite, where `headstart` is genuinely importable — which is exactly how `JobSearch.facets`
    shipped a package-only import that made `/facets` 500 in production only. The route answers
    `except ValueError`, which does not catch it, and the browser's own `.catch` then degraded
    the failure to silence: no counts, no total, no sign anything was wrong.

    Every such import needs a `try: from headstart… / except ImportError: import …` pair. This
    counts them rather than parsing the control flow: an unguarded import is one that has no
    `except ImportError` anywhere near it.
    """
    workflow = WORKFLOW.read_text(encoding="utf-8")
    offenders: list[str] = []
    for name in sorted(_SYNCED_FILE.findall(workflow)):
        if not name.endswith(".py"):
            continue
        source = REPO / "src" / "headstart" / name
        if not source.exists():  # copied from somewhere else in the tree
            continue
        text = source.read_text(encoding="utf-8")
        package_imports = len(_PACKAGE_IMPORT.findall(text))
        fallbacks = text.count("except ImportError")
        if package_imports > fallbacks:
            offenders.append(
                f"{name} ({package_imports} package imports, {fallbacks} fallbacks)"
            )
    assert not offenders, (
        "these modules are synced flat into the Space image but import `headstart` without a "
        f"flat fallback, so they raise ModuleNotFoundError there and only there: {offenders}"
    )

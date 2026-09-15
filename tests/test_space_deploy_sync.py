"""What the Space image installs must match what the workflow stages (ADR-0156).

Before ADR-0156, `deploy-space.yml` synced a hand-curated list of files/dirs into
`deploy/hf-space/`, and the Dockerfile COPYed an independently-maintained,
overlapping-but-not-identical list of the same names. The two lists drifted twice — #115 for
`role_families.json`, #128 for `role_watchlist.json` — and both times a feature silently
stopped working in production: the workflow was green, the image built, the Space booted,
and only the feature quietly did nothing. Six modules also carried a
`try: from headstart import X / except ImportError: import X` shim (plus three more using
`logging.getLogger` in place of `headstart.log.get`), because the flat-copy layout meant
`headstart` wasn't a real installed package inside the Space image.

ADR-0156 fixes the root cause instead of re-curating the list: the Space now installs
`headstart` as a real Python package. `deploy-space.yml` stages the WHOLE `src/headstart`
tree (plus `config/`) into `deploy/hf-space/`, and the Dockerfile COPYs those same two
directories into the image — there is no longer a curated per-file list on either side to
drift apart, and the six shims are gone because `deploy/hf-space/app.py` now imports
`headstart` exactly the way `scripts/ui/serve.py` and this test suite already did.

What this file checks changed to match:

1. The two directories the workflow stages and the two the Dockerfile COPYs are the SAME
   names — the only way #115/#128's failure mode could still occur (a staged directory the
   Dockerfile never copies in).
2. The dual-import/logging shims stay deleted — a regression guard, since nothing else
   would catch the flat-fallback habit creeping back onto one of these four modules now that
   it is pointless.
3. `deploy/hf-space/app.py` and `scripts/ui/serve.py` — the Space and the local dev server —
   pass the SAME context keys to `base.html`, both at the top level and inside the `cfg`
   dict `window.CFG` reads client-side. This is the check that would have caught the
   `has_min_salary` drift this PR fixed (the "Highest salary" sort option silently missing
   from local dev only) and a second one found alongside it in `cfg`
   (`keyword_scopes`/`keyword_default_scope`, which `app.js` reads off `window.CFG`).

Stdlib-only and file-based, so it runs in CI's quality job with no Docker and no YAML
dependency.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "deploy-space.yml"
DOCKERFILE = REPO / "deploy" / "hf-space" / "Dockerfile"
APP = REPO / "deploy" / "hf-space" / "app.py"
SERVE = REPO / "scripts" / "ui" / "serve.py"

# `cp -r <src> deploy/hf-space/<dest>` — the two whole-directory syncs ADR-0156 replaced the
# curated per-file list with.
_STAGED_DIR = re.compile(
    r"^\s*cp\s+-r\s+\S+\s+deploy/hf-space/([\w.-]+)\s*$", re.MULTILINE
)


def _staged_dirs() -> set[str]:
    return set(_STAGED_DIR.findall(WORKFLOW.read_text(encoding="utf-8")))


def _copied_dirs() -> set[str]:
    """Every name on a `COPY <dir> ./<dir>` line in the Dockerfile (single source, dest the
    same name under `./`) — the shape a whole-directory copy takes there."""
    copied: set[str] = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("COPY "):
            continue
        parts = line.split()[1:]
        if len(parts) != 2:
            continue
        src, dest = parts
        if dest.startswith("./") and dest[2:] == src:
            copied.add(src)
    return copied


def test_every_staged_directory_is_copied_into_the_image():
    staged = _staged_dirs()
    assert staged, (
        "no `cp -r <src> deploy/hf-space/<dir>` lines found — did the sync step move?"
    )

    missing = sorted(staged - _copied_dirs())
    assert not missing, (
        f"deploy-space.yml stages {missing} into the Space repo but the Dockerfile never "
        "COPYs them into the image — app.py imports headstart from beside itself, so a "
        "missing directory breaks the import at boot (loudly, unlike #115/#128, which broke "
        "one feature silently back when the sync was a curated file list)"
    )


# The four modules that carried a `try: from headstart import X / except ImportError:` (or,
# for logging, a `logging.getLogger` stand-in) shim until ADR-0156.
_FORMERLY_SHIMMED = {
    "search.py": REPO / "src" / "headstart" / "search.py",
    "facets.py": REPO / "src" / "headstart" / "facets.py",
    "fx.py": REPO / "src" / "headstart" / "fx.py",
    "alerts/store.py": REPO / "src" / "headstart" / "alerts" / "store.py",
}


def test_the_dual_import_shims_stay_deleted():
    """Nothing about the packaging forces a shim to stay gone — if the flat-fallback habit
    creeps back onto one of these four modules, this fails loudly instead of silently
    reintroducing dead code the real package makes pointless."""
    offenders = [
        name
        for name, path in _FORMERLY_SHIMMED.items()
        if "except ImportError" in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"{offenders} still carry a flat-import fallback — the Space installs `headstart` as "
        "a real package (ADR-0156), so these can import it unconditionally"
    )


def _render_template_call(path: Path, template: str) -> ast.Call:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "render_template"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == template
        ):
            return node
    raise AssertionError(f"no render_template({template!r}, ...) call found in {path}")


def _kwarg_names(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg}


def _cfg_dict_keys(call: ast.Call) -> set[str]:
    for kw in call.keywords:
        if kw.arg == "cfg" and isinstance(kw.value, ast.Dict):
            return {
                key.value
                for key in kw.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    raise AssertionError("no `cfg={...}` keyword found on the render_template call")


def test_app_and_serve_pass_the_same_base_html_context():
    app_call = _render_template_call(APP, "base.html")
    serve_call = _render_template_call(SERVE, "base.html")

    app_keys = _kwarg_names(app_call)
    serve_keys = _kwarg_names(serve_call)
    assert app_keys == serve_keys, (
        "app.py and serve.py pass different top-level context keys to base.html — one "
        f"renderer's key silently vanishes from the other's page. Only in app.py: "
        f"{sorted(app_keys - serve_keys)}; only in serve.py: {sorted(serve_keys - app_keys)}"
    )

    app_cfg = _cfg_dict_keys(app_call)
    serve_cfg = _cfg_dict_keys(serve_call)
    assert app_cfg == serve_cfg, (
        "app.py and serve.py pass different keys inside cfg (window.CFG, which app.js reads "
        f"client-side) to base.html. Only in app.py's cfg: {sorted(app_cfg - serve_cfg)}; "
        f"only in serve.py's cfg: {sorted(serve_cfg - app_cfg)}"
    )

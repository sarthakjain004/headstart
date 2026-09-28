"""The role families a tool's `category` names — `headstart/space_mcp/role_families.py` (ADR-0274).

Two things are tested: that every install finds the list (the Space image's `/app/headstart` beside
`/app/config`, and a wheel carrying its own copy, which is what `uvx` builds), each in a fresh
interpreter that imports the package from that layout alone; and how a caller's words for a
category are read.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tomllib

import pytest

from headstart.mcp_protocol.messages import ToolFailure
from headstart.space_mcp import role_families, server

REPO = pathlib.Path(__file__).resolve().parents[1]
FAMILIES = json.loads(
    (REPO / "config" / "role_families.json").read_text(encoding="utf-8")
)
CURRENT = [family["name"] for family in FAMILIES["families"]]

#: Run in the layout under test: where the module found the list, and the ids it read.
_PROBE = (
    "import json, headstart; from headstart.space_mcp import role_families as r; "
    "print(json.dumps({'package': headstart.__file__, 'file': str(r.FILE), "
    "'names': r.names()}))"
)


def _package_copy(site: pathlib.Path) -> None:
    shutil.copytree(
        REPO / "src" / "headstart",
        site / "headstart",
        ignore=shutil.ignore_patterns("__pycache__"),
    )


def _probe(site: pathlib.Path) -> dict:
    """Import the package from ``site`` alone, in a directory with no `config/` above it."""
    done = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
        cwd=site,
        env={**os.environ, "PYTHONPATH": str(site)},
    )
    found = json.loads(done.stdout)
    assert found["package"].startswith(str(site.resolve())), found["package"]
    return found


def test_the_space_image_finds_the_list_in_app_config(tmp_path):
    """The Dockerfile copies `headstart` to `/app/headstart` and `config` to `/app/config`: the
    list is two levels above the package, not three, which is where the hosted server looked."""
    app = tmp_path / "app"
    _package_copy(app)
    (app / "config").mkdir()
    shutil.copy(REPO / "config" / "role_families.json", app / "config")
    found = _probe(app)
    assert found["file"] == str((app / "config" / "role_families.json").resolve())
    assert found["names"] == CURRENT


def test_the_dockerfile_still_lays_the_image_out_that_way():
    dockerfile = (REPO / "deploy" / "hf-space" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "WORKDIR /app" in dockerfile
    assert "COPY headstart ./headstart" in dockerfile
    assert "COPY config ./config" in dockerfile


def test_a_wheel_finds_its_own_copy_beside_the_module(tmp_path):
    """A wheel has no `config/`: pyproject force-includes the list at the path the module reads
    first. The layout here is that wheel's, installed; the build itself is checked in the PR."""
    site = tmp_path / "site-packages"
    _package_copy(site)
    wheel_path = pathlib.Path(
        tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
            "hatch"
        ]["build"]["targets"]["wheel"]["force-include"]["config/role_families.json"]
    )
    shutil.copy(REPO / "config" / "role_families.json", site / wheel_path)
    found = _probe(site)
    assert found["file"] == str((site / wheel_path).resolve())
    assert found["names"] == CURRENT


def test_a_layout_with_no_list_anywhere_leaves_category_a_free_string(tmp_path):
    site = tmp_path / "site-packages"
    _package_copy(site)
    assert _probe(site)["names"] is None


@pytest.mark.parametrize(
    ("asked", "family"),
    [
        ("ai-ml-data-science", "ai-ml-data-science"),
        ("AI, ML & Data Science", "ai-ml-data-science"),
        ("ai, ml & data science", "ai-ml-data-science"),
        ("AI/ML", "ai-ml-data-science"),
        ("machine learning", "ai-ml-data-science"),
        ("Data Science", "ai-ml-data-science"),
        ("ai-ml", "ai-ml-data-science"),
        ("python-development", "software-engineering"),
        ("Security", "security"),
        ("QA & Test", "qa-test"),
    ],
)
def test_an_id_a_label_a_close_name_or_a_retired_id_reads_as_a_current_family(
    asked, family
):
    assert role_families.resolve(asked) == family


def test_a_name_several_families_share_is_refused_naming_them():
    with pytest.raises(ToolFailure) as refused:
        role_families.resolve("data")
    said = str(refused.value)
    assert "data-engineering (Data Engineering)" in said
    assert "ai-ml-data-science (AI, ML & Data Science)" in said
    assert "software-engineering" not in said


def test_an_unknown_name_is_refused_listing_every_current_id_with_its_label():
    with pytest.raises(ToolFailure) as refused:
        role_families.resolve("underwater basket weaving")
    said = str(refused.value)
    assert re.findall(r"([a-z0-9-]+) \(", said) == CURRENT
    for family in FAMILIES["families"]:
        assert f"{family['name']} ({family['label']})" in said


def test_only_current_families_are_offered():
    retired = {family["name"] for family in FAMILIES["retired"]}
    assert role_families.names() == tuple(CURRENT)
    assert not retired & set(role_families.names())


def test_a_value_that_is_not_a_string_is_left_for_the_schema_check():
    assert role_families.resolve(7) == 7


def test_a_name_the_server_cannot_read_is_refused_before_the_space_is_asked():
    class Space:
        def read(self, route, params=()):
            raise AssertionError("the Space was asked")

    with pytest.raises(ToolFailure, match="Send one of these ids"):
        server.call(Space(), "read_trends", {"category": "gardening"})

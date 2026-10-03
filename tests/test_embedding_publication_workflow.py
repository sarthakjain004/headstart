"""The tolerated prune failure must not bypass recovery before a real HF upload."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_PIPELINE = Path(__file__).resolve().parents[1] / ".github/workflows/pipeline.yml"


def _publication_script() -> str:
    step = _PIPELINE.read_text().split("      - name: Upload index state\n", 1)[1]
    step = step.split("      - name: Report publication and fresh coverage\n", 1)[0]
    script = step.split("        run: |\n", 1)[1]
    return re.sub(r"^          ", "", script, flags=re.MULTILINE)


def test_unrecoverable_store_stops_upload_even_after_nonfatal_prune(tmp_path):
    """Execute the actual workflow shell; an invalid store cannot reach any HF command."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    python = bin_dir / "python"
    python.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *embed_merge*--check-store*) exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n"
    )
    python.chmod(0o755)
    hf = bin_dir / "hf"
    hf.write_text('#!/bin/sh\ntouch "$UPLOAD_WAS_REACHED"\nexit 0\n')
    hf.chmod(0o755)
    reached = tmp_path / "upload-reached"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "HF_DATASET": "synthetic-test-dataset",
        "CORPUS": "success",
        "UPLOAD_WAS_REACHED": str(reached),
    }
    result = subprocess.run(
        ["bash", "-e", "-c", _publication_script()],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode != 0
    assert not reached.exists(), result.stdout + result.stderr
    status = (tmp_path / "headstart-publication-status").read_text()
    assert "embedding_store=invalid" in status
    assert "lancedb_index=not_reached" in status

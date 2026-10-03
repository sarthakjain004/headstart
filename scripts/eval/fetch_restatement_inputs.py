"""Fetch one immutable dataset revision for a read-only replay measurement."""

import json
import os
from pathlib import Path

from huggingface_hub import HfApi

from headstart import log
from headstart.ingest import state_fetch


def main():
    log.setup()
    root = Path.cwd()
    repo = os.environ["HF_DATASET"]
    token = os.environ.get("HF_TOKEN")
    info = HfApi(token=token).repo_info(
        repo,
        repo_type="dataset",
        revision=os.environ.get("DATASET_REVISION") or None,
        files_metadata=True,
    )
    if info.siblings is None:
        raise ValueError("dataset returned no file inventory")
    patterns = ["data/facts/*", "data/state/*", "data/descriptions/*", "data/lancedb/*"]
    wanted = state_fetch.remote_matches([s.rfilename for s in info.siblings], patterns)
    if any(not state_fetch.remote_matches(list(wanted), [p]) for p in patterns):
        raise ValueError("replay input directory missing at pinned revision")
    directory = root / "data/restate-diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    inventory = {
        "dataset": repo,
        "revision": info.sha,
        "files": [
            {
                "path": s.rfilename,
                "bytes": s.size,
                "blob_id": s.blob_id,
                "lfs": None if s.lfs is None else vars(s.lfs),
            }
            for s in info.siblings
            if s.rfilename in wanted
        ],
    }
    (directory / "inputs.json").write_text(json.dumps(inventory, indent=2))
    print(f"Replay inputs: {info.sha}; {len(wanted)} files", flush=True)
    state_fetch._download(repo, info.siblings, wanted, token, root, revision=info.sha)
    if missing := state_fetch.absent_locally(wanted, root):
        raise ValueError(f"replay files missing: {missing}")
    print("Pinned replay inputs fetched", flush=True)


if __name__ == "__main__":
    main()

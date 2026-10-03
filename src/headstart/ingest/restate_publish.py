"""Package and atomically publish independently verified, small Trends generations.

Dry run is the default. Only --publish performs HF writes; the workflow separately
requires the parent's release flag. No historical generations are deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from headstart import log
from headstart.trends import restated_history as artifact

IMMUTABLE = tuple(
    f"data/facts/{name}/"
    for name in (
        "job_facts",
        "board_reads",
        "job_vectors",
        "trend_reference",
        "reference_rules",
        "description_facts",
        "description_archive",
    )
)


def _serving_inventory(candidate: Path) -> list[dict]:
    return [
        artifact.file_entry(path, path.relative_to(candidate).as_posix())
        for path in sorted(candidate.rglob("*"))
        if path.is_file() and artifact.allowed(path.relative_to(candidate).as_posix())
    ]


def _trim_company_directory(candidate: Path, source: Path) -> dict:
    import pyarrow.parquet as pq

    from headstart.trends.role_taxonomy import NON_TECH
    from headstart.trends.trend_history import DELTAS

    boards = set()
    for path in (candidate / DELTAS).glob("*.parquet"):
        boards.update(
            row["board"]
            for row in pq.read_table(path, columns=["board", "family"]).to_pylist()
            if row["family"] != NON_TECH
        )
    directory = artifact.read_json(source)
    companies, named = [], []
    for entry in directory["companies"]:
        kept = [board for board in entry["boards"] if board in boards]
        if kept:
            if not isinstance(entry.get("name"), str) or not entry["name"].strip():
                raise ValueError("Company directory lacks a name")
            companies.append(entry | {"boards": kept})
            named.extend(kept)
    if len(named) != len(set(named)) or set(named) != boards:
        raise ValueError(
            "Company directory must cover each generation tech Board exactly once"
        )
    return directory | {"companies": companies}


def prepare(candidate: Path, root: Path) -> None:
    """Add pinned serving labels/config before the independent verifier inventories output."""
    directory = _trim_company_directory(
        candidate, root / "data/state/company_directory.json"
    )
    (candidate / "company_directory.json").write_bytes(artifact.encoded(directory))
    for name in artifact.CONFIG_FILES:
        destination = candidate / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, destination)
    # Check coverage now too; do not fabricate companies for missing Board keys.
    metadata_path = candidate / "replay.json"
    metadata = artifact.read_json(metadata_path)
    artifact.check_history(candidate, metadata)
    metadata["files"] = _serving_inventory(candidate)
    artifact.inventory(metadata["files"], serving=True)
    staged = metadata_path.with_suffix(".json.tmp")
    try:
        staged.write_bytes(artifact.encoded(metadata))
        staged.replace(metadata_path)
    finally:
        staged.unlink(missing_ok=True)
    print(
        "prepared pinned labels and taxonomy for independent verification", flush=True
    )


def package(candidate: Path, destination: Path) -> tuple[Path, dict]:
    metadata = artifact.read_json(candidate / "replay.json")
    if (
        type(metadata.get("schema_version")) is not int
        or metadata["schema_version"] != 1
    ):
        raise ValueError("replay metadata must use schema version 1")
    report = artifact.read_json(candidate / "validation.json")
    files = _serving_inventory(candidate)
    if artifact.inventory(metadata["files"], serving=True) != artifact.inventory(
        files, serving=True
    ):
        raise ValueError("prepared replay file inventory changed before packaging")
    manifest = {
        "schema_version": 1,
        **{
            key: metadata[key]
            for key in (*artifact.IDENTITY, "rules_code_sha", "inputs")
        },
        "files": files,
        "validation": artifact.file_entry(
            candidate / "validation.json", "validation.json"
        ),
        "quality": report.get("quality"),
        "limitations": report.get("limitations"),
        "legacy_history": {"available": True, "recomputed": False, "spliced": False},
    }
    artifact.check_gate(manifest, report)
    artifact.check_history(candidate, metadata)
    generation = hashlib.sha256(artifact.encoded(manifest)).hexdigest()
    manifest["generation"] = generation
    directory = destination / artifact.ROOT / "generations" / generation
    if directory.exists():
        raise ValueError(
            "package destination already exists; use a fresh output directory"
        )
    directory.mkdir(parents=True)
    for entry in [*files, manifest["validation"]]:
        target = directory / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate / entry["path"], target)
    (directory / "manifest.json").write_bytes(artifact.encoded(manifest))
    pointer = {
        "schema_version": 1,
        **{
            key: manifest[key]
            for key in (
                "generation",
                "rules_code_sha",
                "rules_fingerprint",
                "last_covered_tick",
            )
        },
        "manifest_sha256": artifact.sha256(directory / "manifest.json"),
    }
    (destination / artifact.CURRENT).write_bytes(artifact.encoded(pointer))
    artifact.validate_generation(directory, pointer)
    print(
        f"packaged {generation}: {len(files)} files, {sum(e['size'] for e in files)} bytes",
        flush=True,
    )
    return directory, pointer


def _ancestor(older: str, newer: str) -> bool:
    if older == newer:
        return True
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer], check=False
    )
    if result.returncode not in (0, 1):
        raise ValueError("cannot establish rules revision ancestry")
    return result.returncode == 0


def check_newer(current: dict | None, candidate: dict, ancestor=_ancestor) -> None:
    if current is None or current["generation"] == candidate["generation"]:
        return
    if current["last_covered_tick"] > candidate["last_covered_tick"]:
        raise ValueError("refusing to replace newer covered history")
    if not ancestor(current["rules_code_sha"], candidate["rules_code_sha"]):
        raise ValueError("refusing older or incomparable rules revision")
    if (
        current["last_covered_tick"] == candidate["last_covered_tick"]
        and current["rules_code_sha"] == candidate["rules_code_sha"]
    ):
        raise ValueError("different generation at identical rules revision and tick")


def check_inputs(inputs: list[dict], siblings: list) -> None:
    """Check chosen immutable content only. Mutable fetched state is pinned, not frozen."""
    remote = {s.rfilename: s for s in siblings}
    checked = 0
    for entry in inputs:
        if not entry["path"].startswith(IMMUTABLE):
            continue
        item = remote.get(entry["path"])
        if item is None or item.size != entry["size"]:
            raise ValueError(
                f"chosen immutable input disappeared/changed: {entry['path']}"
            )
        digest = getattr(item, "lfs", None)
        if digest:
            actual = digest.get("sha256") if isinstance(digest, dict) else digest.sha256
            matches = actual == entry["sha256"]
        else:
            matches = bool(entry.get("git_blob")) and item.blob_id == entry["git_blob"]
        if not matches:
            raise ValueError(f"chosen immutable input hash changed: {entry['path']}")
        checked += 1
    if not checked:
        raise ValueError("no chosen immutable facts/reference inputs")


def publish(
    repo: str, root: Path, pointer: dict, *, api=None, download=None, attempts: int = 3
) -> str:
    from huggingface_hub import CommitOperationAdd, HfApi, hf_hub_download
    from huggingface_hub.errors import (
        EntryNotFoundError,
        HfHubHTTPError,
        LocalEntryNotFoundError,
    )

    api = api or HfApi(token=os.environ.get("HF_TOKEN"))
    download = download or hf_hub_download
    directory = root / artifact.ROOT / "generations" / pointer["generation"]
    manifest = artifact.validate_generation(directory, pointer)
    for attempt in range(attempts):
        tip = api.repo_info(repo, repo_type="dataset", files_metadata=True)
        try:
            previous = artifact.read_json(
                Path(
                    download(
                        repo, artifact.CURRENT, repo_type="dataset", revision=tip.sha
                    )
                )
            )
        except LocalEntryNotFoundError:
            raise
        except EntryNotFoundError:
            previous = None
        check_newer(previous, pointer)
        if previous and previous["generation"] == pointer["generation"]:
            print("generation already current", flush=True)
            return tip.sha
        check_inputs(manifest["inputs"], tip.siblings)
        paths = [*sorted(directory.rglob("*")), root / artifact.CURRENT]
        operations = [
            CommitOperationAdd(
                path_in_repo=p.relative_to(root).as_posix(), path_or_fileobj=p
            )
            for p in paths
            if p.is_file()
        ]
        print(
            f"publishing generation {pointer['generation']} against {tip.sha}, attempt {attempt + 1}",
            flush=True,
        )
        try:
            result = api.create_commit(
                repo_id=repo,
                repo_type="dataset",
                parent_commit=tip.sha,
                operations=operations,
                commit_message="Publish verified restated Trends generation",
            )
            return result.oid
        except HfHubHTTPError as exc:
            if (
                getattr(exc.response, "status_code", None) not in (409, 412)
                or attempt + 1 == attempts
            ):
                raise
            print(
                "dataset advanced; rechecking chosen content and current manifest",
                flush=True,
            )
    raise RuntimeError("publication attempts exhausted")


def fetch(repo: str, root: Path, revision: str | None = None) -> dict:
    """Fetch pinned baseline replay inputs; bound historical pieces never read live LanceDB."""
    from huggingface_hub import HfApi, get_token, hf_hub_url

    from headstart.ingest.restate_baseline import committed_baseline
    from headstart.ingest.state_fetch import _fetch_ranged, _fetch_whole

    token = os.environ.get("HF_TOKEN") or get_token()
    tip = HfApi(token=token).repo_info(
        repo, repo_type="dataset", revision=revision, files_metadata=True
    )
    files = [
        s
        for s in tip.siblings
        if s.rfilename.startswith(("data/facts/", "data/state/", "data/descriptions/"))
    ]
    if not files:
        raise ValueError("no replay input files at the pinned dataset revision")
    checkpoint = "data/state/reference_state.parquet"
    reference_prefix = "data/facts/trend_reference/"
    if not any(s.rfilename == checkpoint for s in files) or not any(
        s.rfilename.startswith(reference_prefix) for s in files
    ):
        raise ValueError("pinned dataset lacks a committed reference baseline")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def one(item):
        path = root / item.rfilename
        path.parent.mkdir(parents=True, exist_ok=True)
        url = hf_hub_url(repo, item.rfilename, repo_type="dataset", revision=tip.sha)
        (_fetch_ranged if item.size >= 100_000_000 else _fetch_whole)(
            url, path, item.size, headers
        )
        entry = artifact.file_entry(path, item.rfilename)
        if not getattr(item, "lfs", None):
            digest = hashlib.sha1(usedforsecurity=False)
            digest.update(f"blob {entry['size']}\0".encode())
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            entry["git_blob"] = digest.hexdigest()
            if entry["git_blob"] != item.blob_id:
                raise ValueError("pinned input blob hash mismatch")
        else:
            remote = (
                item.lfs.sha256
                if not isinstance(item.lfs, dict)
                else item.lfs["sha256"]
            )
            if remote != entry["sha256"]:
                raise ValueError("pinned input content hash mismatch")
        print(f"fetched {item.rfilename}: {entry['size']} bytes", flush=True)
        return entry

    entries = []
    prerequisites = [
        s
        for s in files
        if s.rfilename == checkpoint or s.rfilename.startswith(reference_prefix)
    ]
    remaining = [s for s in files if s not in prerequisites]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(one, item) for item in prerequisites]):
            entries.append(future.result())
        try:
            baseline = committed_baseline(root / "data/facts", root / "data/state")
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid committed reference baseline: {exc}") from exc
        if baseline is None:
            raise ValueError("pinned dataset lacks a committed reference baseline")
        print(f"validated committed baseline: {baseline.name}", flush=True)
        for future in as_completed([pool.submit(one, item) for item in remaining]):
            entries.append(future.result())
    value = {
        "input_revision": tip.sha,
        "inputs": sorted(entries, key=lambda e: e["path"]),
    }
    (root / "data/restated-inputs.json").write_bytes(artifact.encoded(value))
    if os.environ.get("GITHUB_ENV"):
        with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
            stream.write(f"HEADSTART_RESTATE_INPUT_REVISION={tip.sha}\n")
            stream.write(
                "HEADSTART_RESTATE_INPUT_INVENTORY=data/restated-inputs.json\n"
            )
    return value


def main() -> int:
    log.setup()
    log.context("restate_publish")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "prepare", "package"))
    parser.add_argument("--repo", default="imPoseidon/headstart-index")
    parser.add_argument(
        "--revision", help="dataset revision for fetch; defaults to current head"
    )
    parser.add_argument("--candidate", type=Path, default=Path("data/restated"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=Path("data/restated-publication"))
    parser.add_argument(
        "--publish", action="store_true", help="explicitly authorize the HF commit"
    )
    args = parser.parse_args()
    if args.publish and args.command != "package":
        parser.error("--publish requires package")
    if args.command == "fetch":
        fetch(args.repo, args.root, args.revision or None)
    elif args.command == "prepare":
        prepare(args.candidate, args.root)
    else:
        _, pointer = package(args.candidate, args.out)
        if args.publish:
            print(publish(args.repo, args.out, pointer), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

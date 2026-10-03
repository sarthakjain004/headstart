"""Verified, immutable Trends generations; legacy history remains a separate selection.

Shared artifact validation lives here so publication and Space use the same contract.
No facts, descriptions, vectors or per-id placements belong in a serving generation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from headstart.trends import trend_history
from headstart.trends.role_taxonomy import NON_TECH

ROOT = "data/trends/restated"
CURRENT = f"{ROOT}/current.json"
MAX_BYTES = 128 * 1024 * 1024
MAX_CONTROL_BYTES = 16 * 1024 * 1024
IDENTITY = (
    "rules_fingerprint",
    "input_revision",
    "first_covered_tick",
    "last_covered_tick",
)
CONFIG_FILES = ("config/role_families.json", "config/role_watchlist.json")


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_entry(path: Path, name: str) -> dict:
    return {"path": name, "size": path.stat().st_size, "sha256": sha256(path)}


def safe_path(name: str) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and not PurePosixPath(name).is_absolute()
        and ".." not in PurePosixPath(name).parts
        and str(PurePosixPath(name)) == name
        and "\\" not in name
    )


def allowed(name: str) -> bool:
    return name in (*CONFIG_FILES, "company_directory.json", "dedup_evictions.csv") or (
        safe_path(name)
        and len(PurePosixPath(name).parts) == 2
        and PurePosixPath(name).parts[0] == trend_history.DELTAS
        and name.endswith(".parquet")
    )


def inventory(entries: list[dict], *, serving: bool = False) -> dict[str, dict]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("empty file inventory")
    found = {}
    for entry in entries:
        name = entry["path"]
        if (
            not safe_path(name)
            or (serving and not allowed(name))
            or name in found
            or type(entry["size"]) is not int
            or entry["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise ValueError(f"invalid inventory entry: {name}")
        found[name] = entry
    if serving:
        if sum(e["size"] for e in found.values()) > MAX_BYTES:
            raise ValueError("history artifact exceeds size limit")
        if (
            not set(CONFIG_FILES).issubset(found)
            or "company_directory.json" not in found
        ):
            raise ValueError("history lacks pinned taxonomy or Company directory")
        if not any(n.startswith(trend_history.DELTAS + "/") for n in found):
            raise ValueError("history lacks ticks")
    return found


def check_gate(manifest: dict, report: dict) -> None:
    if (
        type(manifest.get("schema_version")) is not int
        or type(report.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or report.get("schema_version") != 1
        or report.get("complete") is not True
        or report.get("pass") is not True
    ):
        raise ValueError("independent verification is not complete/pass version 1")
    for key in IDENTITY:
        if not manifest.get(key) or manifest[key] != report.get(key):
            raise ValueError(f"verification identity mismatch: {key}")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", manifest["rules_fingerprint"])
        or not re.fullmatch(r"[0-9a-f]{40}", manifest["input_revision"])
        or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("rules_code_sha", ""))
    ):
        raise ValueError("invalid rules/input revision identity")
    first = datetime.fromisoformat(manifest["first_covered_tick"])
    last = datetime.fromisoformat(manifest["last_covered_tick"])
    if first.tzinfo is None or last.tzinfo is None or first > last:
        raise ValueError("invalid covered tick bounds")
    for key in ("files", "inputs"):
        a = inventory(manifest[key], serving=key == "files")
        b = inventory(report[key], serving=key == "files")
        if a != b:
            raise ValueError(f"verification inventory mismatch: {key}")
    verifier = report.get("verifier", {})
    if not verifier.get("name") or not re.fullmatch(
        r"[0-9a-f]{40}", verifier.get("code_sha", "")
    ):
        raise ValueError("independent verifier identity absent")
    quality = report.get("quality", {})
    metrics = quality.get("supported_metrics")
    if not isinstance(metrics, list) or "stock" not in metrics:
        raise ValueError(
            "verified scope must declare supported_metrics including stock"
        )
    if not isinstance(report.get("limitations"), list) or not report.get("checks"):
        raise ValueError("verification lacks checks or explicit limitations")
    if (
        manifest.get("quality") != quality
        or manifest.get("limitations") != report["limitations"]
    ):
        raise ValueError("verification quality/limitations mismatch")


def read_json(path: Path) -> dict:
    if path.stat().st_size > MAX_CONTROL_BYTES:
        raise ValueError("control document exceeds size limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("control document is not an object")
    return value


def validate_generation(directory: Path, pointer: dict) -> dict:
    manifest_path = directory / "manifest.json"
    if (
        pointer.get("schema_version") != 1
        or sha256(manifest_path) != pointer["manifest_sha256"]
    ):
        raise ValueError("current manifest hash/version mismatch")
    manifest = read_json(manifest_path)
    payload = {k: v for k, v in manifest.items() if k != "generation"}
    if hashlib.sha256(encoded(payload)).hexdigest() != manifest["generation"]:
        raise ValueError("generation content identity mismatch")
    for key in (
        "generation",
        "rules_fingerprint",
        "rules_code_sha",
        "last_covered_tick",
    ):
        if manifest[key] != pointer.get(key):
            raise ValueError(f"pointer identity mismatch: {key}")
    report_entry = manifest["validation"]
    if report_entry["path"] != "validation.json":
        raise ValueError("unexpected report path")
    report_path = directory / "validation.json"
    if file_entry(report_path, "validation.json") != report_entry:
        raise ValueError("verification report hash mismatch")
    check_gate(manifest, read_json(report_path))
    if manifest.get("legacy_history") != {
        "available": True,
        "recomputed": False,
        "spliced": False,
    }:
        raise ValueError("invalid legacy history declaration")
    expected = inventory(manifest["files"], serving=True)
    actual = {
        p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()
    }
    if actual != set(expected) | {"manifest.json", "validation.json"}:
        raise ValueError("generation contains missing or extra files")
    if sum((directory / n).stat().st_size for n in actual) > MAX_BYTES:
        raise ValueError("complete generation exceeds size limit")
    for name, entry in expected.items():
        path = directory / name
        if path.is_symlink() or file_entry(path, name) != entry:
            raise ValueError(f"artifact hash mismatch: {name}")
    check_history(directory, manifest)
    return manifest


def check_history(directory: Path, metadata: dict) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    stamps, boards = [], set()
    for path in sorted((directory / trend_history.DELTAS).glob("*.parquet")):
        table = pq.read_table(path)
        if tuple(table.column_names) != trend_history.TICK_COLUMNS:
            raise ValueError("invalid tick columns")
        if any(
            table.schema.field(n).type != (pa.int64() if n == "delta" else pa.string())
            for n in trend_history.TICK_COLUMNS
        ) or any(table[n].null_count for n in trend_history.TICK_COLUMNS):
            raise ValueError("invalid tick types or nulls")
        stamps.append((table.schema.metadata or {})[b"ts"].decode())
        boards.update(
            board
            for board, family in zip(
                table["board"].to_pylist(), table["family"].to_pylist(), strict=True
            )
            if family != NON_TECH
        )
        if not (table.schema.metadata or {}).get(b"methodology"):
            raise ValueError("tick lacks methodology")
    if stamps != sorted(set(stamps)) or not stamps:
        raise ValueError("empty, duplicate or unordered ticks")
    if (stamps[0], stamps[-1]) != (
        metadata["first_covered_tick"],
        metadata["last_covered_tick"],
    ):
        raise ValueError("history tick bounds mismatch")
    directory_json = read_json(directory / "company_directory.json")
    if not directory_json["companies"] or any(
        not entry.get("name") or not entry.get("boards")
        for entry in directory_json["companies"]
    ):
        raise ValueError("invalid Company directory")
    covered = {b for entry in directory_json["companies"] for b in entry["boards"]}
    if not boards.issubset(covered):
        raise ValueError("Company directory does not cover generation tech Boards")


def pull(repo: str, local: Path, token: str | None = None) -> None:
    """Pin one dataset revision and fetch only its pointer's small, allowlisted generation."""
    from huggingface_hub import HfApi, hf_hub_download

    revision = HfApi(token=token).repo_info(repo, repo_type="dataset").sha

    def download(name: str) -> Path:
        return Path(
            hf_hub_download(
                repo,
                name,
                repo_type="dataset",
                revision=revision,
                local_dir=local,
                token=token,
            )
        )

    pointer = read_json(download(CURRENT))
    generation = pointer["generation"]
    if not re.fullmatch(r"[0-9a-f]{64}", generation):
        raise ValueError("invalid generation name")
    prefix = f"{ROOT}/generations/{generation}"
    manifest_path = download(f"{prefix}/manifest.json")
    if sha256(manifest_path) != pointer["manifest_sha256"]:
        raise ValueError("downloaded manifest hash mismatch")
    manifest = read_json(manifest_path)
    files = inventory(manifest["files"], serving=True)
    report = manifest["validation"]
    if report["path"] != "validation.json" or report["size"] > MAX_CONTROL_BYTES:
        raise ValueError("invalid validation document")
    if (
        sum(e["size"] for e in files.values())
        + report["size"]
        + manifest_path.stat().st_size
        > MAX_BYTES
    ):
        raise ValueError("complete generation exceeds size limit")
    for name in ["validation.json", *files]:
        download(f"{prefix}/{name}")
    validate_generation(local / prefix, pointer)


@dataclass
class Selection:
    legacy: trend_history.TrendHistory
    restated: trend_history.TrendHistory | None = None
    manifest: dict | None = None
    reason: str | None = None

    def choose(self, name: str = "preferred") -> trend_history.TrendHistory:
        if name not in ("preferred", "legacy", "restated"):
            raise ValueError("history must be preferred, restated or legacy")
        if name == "restated" and self.restated is None:
            raise trend_history.TrendsUnavailable("no verified restated history")
        return self.legacy if name == "legacy" else self.restated or self.legacy

    def provenance(self, history: trend_history.TrendHistory) -> dict:
        if history is self.restated:
            return {
                "kind": "restated",
                "recomputed": True,
                "spliced": False,
                **{
                    k: self.manifest[k]
                    for k in (
                        *IDENTITY,
                        "generation",
                        "rules_code_sha",
                        "quality",
                        "limitations",
                    )
                },
                "rules_status": "verified_at_code_sha",
                "legacy_available": bool(self.legacy.ticks),
            }
        return {
            "kind": "legacy",
            "recomputed": False,
            "spliced": False,
            "fallback_reason": self.reason,
            "limitations": ["Historical rules were not recomputed."],
        }

    def answer(self, history: trend_history.TrendHistory, question) -> dict:
        if history is self.restated:
            supported = self.manifest["quality"]["supported_metrics"]
            if question.metric not in supported or (
                (
                    question.split == "roles"
                    or (question.family or "").startswith("watch:")
                )
                and "watched_roles" not in supported
            ):
                raise trend_history.TrendsUnavailable(
                    "this generation does not certify the requested metric"
                )
        answer = history.unnetted_answer(question)
        if (
            history is self.restated
            and "turnover" not in self.manifest["quality"]["supported_metrics"]
        ):
            for line in answer.get("series", []):
                line.pop("turnover", None)
            answer["turnover_since"] = None
            answer["closures_unseen"] = {}
            answer["closures_uncounted"] = []
            answer["pick_turnover"] = {}
        answer["history"] = self.provenance(history)
        return answer


def load(local: Path, config: Path, legacy: trend_history.TrendHistory) -> Selection:
    import pyarrow.parquet as pq

    result = Selection(legacy)
    try:
        pointer = read_json(local / CURRENT)
        generation = pointer["generation"]
        if not re.fullmatch(r"[0-9a-f]{64}", generation):
            raise ValueError("invalid generation name")
        directory = local / ROOT / "generations" / generation
        result.manifest = validate_generation(directory, pointer)
        result.restated = trend_history.TrendHistory.load(
            directory, directory / "config"
        )
        if list(result.restated.ticks) != sorted(
            pq.read_schema(p).metadata[b"ts"].decode()
            for p in (directory / trend_history.DELTAS).glob("*.parquet")
        ):
            raise ValueError("generation reader did not load all ticks")
    except Exception as exc:  # noqa: BLE001 - invalid generations fall back without costing Search
        result.restated, result.manifest = None, None
        result.reason = f"{type(exc).__name__}: {exc}"
        print(f"restated history unavailable: {result.reason}", flush=True)
    return result

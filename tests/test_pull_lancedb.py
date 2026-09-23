"""The chunk-size guard in scripts/fetch/pull_lancedb.py (ADR-0085).

A big file is fetched as absolute-offset chunks, so resuming under a different ``--chunk-mb``
would splice bytes from the wrong offsets. The guard used to infer that from each chunk's size,
which cannot tell a *short* chunk (a fetch that ran out of retries — the normal thing a resume
exists for) from one carved at another size. On 2026-09-23 it refused to resume a 2.9 GB pull
over one 28,311,552-byte chunk 31, with the same ``--chunk-mb`` both times.
"""

import importlib.util
import pathlib

import pytest

pytest.importorskip("huggingface_hub")

_SPEC = importlib.util.spec_from_file_location(
    "pull_lancedb",
    pathlib.Path(__file__).resolve().parents[1]
    / "scripts"
    / "fetch"
    / "pull_lancedb.py",
)
pull = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pull)

CHUNK = 32


def _plan(size: int = 100):
    return pull._chunks(size, CHUNK)


def test_a_fresh_transfer_records_its_chunk_size(tmp_path):
    dest = tmp_path / "f.lance"
    pull._claim_chunk_size(dest, _plan(), CHUNK)
    assert pull._chunk_size_marker(dest).read_text() == str(CHUNK)


def test_a_short_chunk_from_the_same_chunk_size_resumes(tmp_path):
    dest = tmp_path / "f.lance"
    pull._claim_chunk_size(dest, _plan(), CHUNK)
    pull._chunk_path(dest, 0).write_bytes(b"x" * CHUNK)  # complete
    pull._chunk_path(dest, 1).write_bytes(b"x" * 28)  # cut short by a failed fetch
    pull._claim_chunk_size(dest, _plan(), CHUNK)  # must not raise


def test_a_different_chunk_size_is_refused(tmp_path):
    dest = tmp_path / "f.lance"
    pull._claim_chunk_size(dest, _plan(), CHUNK)
    pull._chunk_path(dest, 0).write_bytes(b"x" * CHUNK)
    with pytest.raises(SystemExit, match="--chunk-mb"):
        pull._claim_chunk_size(dest, pull._chunks(100, 16), 16)


def test_chunks_without_a_marker_keep_the_size_check(tmp_path):
    """A transfer started before the marker existed has only chunk sizes to go on."""
    dest = tmp_path / "f.lance"
    pull._chunk_path(dest, 0).write_bytes(b"x" * 16)  # carved at 16, now resumed at 32
    with pytest.raises(SystemExit, match="--chunk-mb"):
        pull._claim_chunk_size(dest, _plan(), CHUNK)


def test_an_unmarked_transfer_with_a_proven_chunk_size_resumes_its_short_chunk(
    tmp_path,
):
    """The exact 2026-09-23 state: complete chunks at the current size, one cut short."""
    dest = tmp_path / "f.lance"
    pull._chunk_path(dest, 0).write_bytes(b"x" * CHUNK)  # proves the size in use
    pull._chunk_path(dest, 1).write_bytes(b"x" * 28)
    pull._claim_chunk_size(dest, _plan(), CHUNK)  # must not raise
    assert pull._chunk_size_marker(dest).read_text() == str(CHUNK)


def test_an_unmarked_chunk_larger_than_the_size_is_refused(tmp_path):
    dest = tmp_path / "f.lance"
    pull._chunk_path(dest, 0).write_bytes(b"x" * CHUNK)
    pull._chunk_path(dest, 1).write_bytes(b"x" * 64)  # carved at 64
    with pytest.raises(SystemExit, match="--chunk-mb"):
        pull._claim_chunk_size(dest, pull._chunks(200, CHUNK), CHUNK)


class _RangedBody:
    """A ranged GET against an in-memory file — what the HF resolve endpoint returns."""

    def __init__(self, data: bytes, range_header: str):
        lo, hi = range_header.removeprefix("bytes=").split("-")
        self._body = data[int(lo) : int(hi) + 1]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield from (self._body[i : i + n] for i in range(0, len(self._body), n))


def test_fetch_big_resumes_a_short_chunk_byte_exact(tmp_path, monkeypatch):
    """End to end from the 2026-09-23 state: no marker, chunk 0 whole, chunk 1 cut short."""
    data = bytes(range(256)) * 4  # 1,024 bytes
    size, chunk = len(data), 256

    class Session:
        def get(self, url, headers, **_):
            return _RangedBody(data, headers["Range"])

    monkeypatch.setattr(pull, "_session", Session)
    monkeypatch.setattr(pull, "hf_hub_url", lambda *a, **k: "https://example.invalid/f")
    monkeypatch.setattr(pull, "get_token", lambda: "t")
    dest = tmp_path / "data" / "f.lance"
    dest.parent.mkdir(parents=True)
    pull._chunk_path(dest, 0).write_bytes(data[:256])
    pull._chunk_path(dest, 1).write_bytes(data[256:300])

    pull.fetch_big(tmp_path, "data/f.lance", size, workers=2, chunk=chunk)

    assert dest.read_bytes() == data
    assert not pull._chunk_size_marker(dest).exists()

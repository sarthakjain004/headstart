"""The chunk-size guard in scripts/fetch/pull_lancedb.py (ADR-0085).

A big file is fetched as absolute-offset chunks, so resuming under a different ``--chunk-mb``
would splice bytes from the wrong offsets. The guard used to infer that from each chunk's size,
which cannot tell a *short* chunk (a fetch that ran out of retries — the normal thing a resume
exists for) from one carved at another size. On 2026-09-23 it refused to resume a 2.9 GB pull
over one 28,311,552-byte chunk 31, with the same ``--chunk-mb`` both times. A transfer now
records its chunk size before it writes a byte, and a resume compares that.

Most tests drive ``fetch_big`` end to end against an in-memory ranged server.
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

DATA = bytes(range(256)) * 4  # 1,024 bytes
PATH = "data/f.lance"


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


@pytest.fixture
def dest(tmp_path, monkeypatch):
    class Session:
        def get(self, url, headers, **_):
            return _RangedBody(DATA, headers["Range"])

    monkeypatch.setattr(pull, "_session", Session)
    monkeypatch.setattr(pull, "hf_hub_url", lambda *a, **k: "https://example.invalid/f")
    monkeypatch.setattr(pull, "get_token", lambda: "t")
    d = tmp_path / PATH
    d.parent.mkdir(parents=True)
    return d


def _fetch(dest, chunk):
    pull.fetch_big(dest.parents[1], PATH, len(DATA), workers=2, chunk=chunk)


def test_a_fresh_transfer_lands_byte_exact_and_clears_its_record(dest):
    _fetch(dest, 256)
    assert dest.read_bytes() == DATA
    assert not pull._chunk_size_marker(dest).exists()


def test_a_recorded_transfer_resumes_a_short_chunk(dest):
    """The bug: a fetch that ran out of retries left chunk 1 short, same --chunk-mb."""
    pull._record_chunk_size(dest, 256)
    pull._chunk_path(dest, 0).write_bytes(DATA[:256])
    pull._chunk_path(dest, 1).write_bytes(DATA[256:300])
    _fetch(dest, 256)
    assert dest.read_bytes() == DATA


def test_a_recorded_transfer_refuses_another_chunk_size(dest):
    pull._record_chunk_size(dest, 128)
    pull._chunk_path(dest, 0).write_bytes(DATA[:128])
    with pytest.raises(SystemExit, match="--chunk-mb"):
        _fetch(dest, 256)
    assert not dest.exists()


def test_an_unreadable_record_is_refused_with_a_way_out(dest):
    pull._chunk_size_marker(dest).write_text("")
    with pytest.raises(SystemExit, match="unreadable"):
        _fetch(dest, 256)


def test_an_unrecorded_short_chunk_is_refused_naming_the_file_to_delete(dest):
    """A transfer from before the record: sizes alone cannot say short from other-size."""
    pull._chunk_path(dest, 0).write_bytes(DATA[:256])
    pull._chunk_path(dest, 1).write_bytes(DATA[256:300])
    with pytest.raises(SystemExit, match=pull._chunk_path(dest, 1).name):
        _fetch(dest, 256)
    pull._chunk_path(dest, 1).unlink()  # the remedy the message names
    _fetch(dest, 256)
    assert dest.read_bytes() == DATA


def test_an_unrecorded_concat_at_another_size_is_refused_not_corrupted(dest):
    """Review of #574: the recovery carves the .part at the NEW size, so the size check must
    run after it — run before, stale old-size chunks assembled a corrupt file of the right
    length that the final size check could not catch."""
    old = 128
    joined = dest.with_name(dest.name + ".part")
    joined.write_bytes(DATA[: 3 * old])  # killed mid-concat after 3 old chunks
    for i in range(3, len(DATA) // old):
        pull._chunk_path(dest, i).write_bytes(DATA[i * old : (i + 1) * old])
    with pytest.raises(SystemExit, match="--chunk-mb"):
        _fetch(dest, 256)
    assert not dest.exists()


def test_the_record_is_written_whole_or_not_at_all(dest):
    pull._record_chunk_size(dest, 256)
    assert pull._chunk_size_marker(dest).read_text() == "256"
    assert not list(dest.parent.glob("*.tmp"))

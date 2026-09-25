#!/usr/bin/env python3
"""Train the role-family classifier head the pipeline serves (ADR-0220, ADR-0222). A deliberate,
one-off fit.

The head is a logistic regression over two inputs side by side: the JobBERT-v2 title embedding
(the encoder the pipeline runs, through ``role_family_classifier.encode``) and the row's served
description ``vector``, read from the ``--table`` snapshot. It is trained on two kinds of label:

- **silver**: distinct served titles the title rules (``role_family_title_rules.py``, beside this
  script) decide, at most ``--per-family`` per family, each with the vector of one served row that
  carries the title. The rules are labelling functions here, not the answer, and the head
  generalises past the titles they decide;
- **dev gold**: Jobs labelled by hand against the taxonomy rubric, given weight ``w``.

Two things are chosen by 5-fold cross-validation over dev gold, grouped by copy (company and
title), so no choice is scored on a Job its model trained on:

- ``w``;
- the class priors the head's bias is shifted to: balanced, as silver is drawn, or the rules'
  mix over served rows. The priors matter because silver is balanced while served rows are not.

The abstain cutoff is then chosen from the chosen candidate's out-of-fold probabilities on the
**uniform** dev rows, the ones drawn like served rows: the best accuracy on covered rows subject
to covering ``--min-coverage`` of them.

``--test`` is read once, after every choice is fixed, and only to report.

Writes ``config/role_family_classifier/manifest.json`` and ``head.npz``, the weights split into the
title part and the row-vector part the pipeline adds together. A new head needs a new
``--version``: every Trends series re-bases on it, and the pipeline's title cache is discarded.

Run: python scripts/embed/train_role_family_classifier.py --table DB --dev D [--test X] --version N
  --table   a LanceDB directory holding the served ``jobs`` table the gold was drawn from: silver,
            the priors and every row vector come from it
  --dev     parquet with ``id``, ``title``, ``gold``, ``copy_key`` and ``sample_source``
            (``uniform``/other)
  --test    parquet with ``id``, ``title`` and ``gold``
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))
from headstart import roles
from headstart.embedding_conventions import MODEL as ROW_VECTOR_MODEL
from headstart.embedding_conventions import PROD_TABLE
from headstart.ingest import role_family_classifier
from headstart.roles import NON_TECH

_MODEL = "TechWolf/JobBERT-v2"
_MODEL_REVISION = (
    "a480476925abdf9d97621e56aa38abbb572fe343"  # pinned: a new revision is a new head
)
_HEAD_DIR = REPO_ROOT / "config" / "role_family_classifier"
_C = 0.25  # dev accuracy was flat across C in 0.25..16 (2026-09-25), so it is not searched
_DEV_WEIGHTS = (0.0, 5.0, 20.0)
_PRIORS = ("balanced", "served-rows")
_CUTOFF_GRID = tuple(round(x, 2) for x in np.arange(0.0, 0.96, 0.05))


def _title_rules():
    """The title rules live beside this script, which is their only caller."""
    path = Path(__file__).with_name("role_family_title_rules.py")
    spec = importlib.util.spec_from_file_location("role_family_title_rules", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _encode(titles: list[str]) -> np.ndarray:
    normalised = [role_family_classifier.normalise(t) for t in titles]
    return role_family_classifier.encode(normalised, _MODEL, _MODEL_REVISION)


def served_rows(table_dir: Path) -> pd.DataFrame:
    """``id`` and ``title`` of every served row in the snapshot."""
    import lancedb

    table = lancedb.connect(str(table_dir)).open_table(PROD_TABLE)
    return table.search().select(["id", "title"]).limit(table.count_rows()).to_pandas()


def row_vectors(table_dir: Path, ids: list[str]) -> np.ndarray:
    """The served ``vector`` of each id, in order. Every id must be in the snapshot: a head
    trained without a gold row's description would silently learn from less than it claims."""
    import lancedb

    table = lancedb.connect(str(table_dir)).open_table(PROD_TABLE)
    wanted = {job_id: i for i, job_id in enumerate(ids)}
    out: np.ndarray | None = None
    found = 0
    for batch in (
        table.search()
        .select(["id", "vector"])
        .limit(table.count_rows())
        .to_batches(65536)
    ):
        vectors = batch.column("vector").flatten().to_numpy().reshape(len(batch), -1)
        if out is None:
            out = np.zeros((len(ids), vectors.shape[1]), dtype=np.float32)
        for j, job_id in enumerate(batch.column("id").to_pylist()):
            if job_id in wanted:
                out[wanted[job_id]] = vectors[j]
                found += 1
    if found != len(wanted):
        raise SystemExit(
            f"{len(wanted) - found} of {len(wanted)} rows are not in {table_dir}: train against "
            "the snapshot the gold was drawn from"
        )
    return out


def silver_and_served_mix(
    rows: pd.DataFrame, per_family: int, seed: int
) -> tuple[pd.DataFrame, pd.Series]:
    """Silver titles capped per family, each with the ``id`` of one random served row carrying
    it (for its vector), and the rules' verdict mix over served rows (the priors)."""
    titles = rows.title
    rules = _title_rules()
    rules.check_families(
        set(roles.load_families(REPO_ROOT / "config" / "role_families.json"))
    )
    verdict_of = {
        t: rules.classify(t).family
        # sorted: a set's order changes per process, and it decides which titles the draw keeps
        for t in sorted({role_family_classifier.normalise(t) for t in titles if t})
    }
    labelled = pd.DataFrame(verdict_of.items(), columns=["title", "family"]).dropna()
    silver = (
        labelled.sample(frac=1.0, random_state=seed)
        .groupby("family")
        .head(per_family)
        .reset_index(drop=True)
    )
    served = pd.Series(
        [verdict_of.get(role_family_classifier.normalise(t)) for t in titles if t]
    ).dropna()
    normalised = rows.assign(ntitle=rows.title.map(role_family_classifier.normalise))
    one_row = (
        normalised.sample(frac=1.0, random_state=seed)
        .drop_duplicates("ntitle")
        .set_index("ntitle")
        .id
    )
    silver["id"] = silver.title.map(one_row)
    print(
        f"silver: {len(silver)} titles from {len(labelled)} the rules decide; per family "
        f"{dict(Counter(silver.family))}",
        flush=True,
    )
    return silver, served.value_counts(normalize=True)


def fit(
    x: np.ndarray,
    y: np.ndarray,
    weights: np.ndarray,
    priors: str,
    served_mix: pd.Series,
):
    """The fitted model and the per-class bias shift that moves its priors to ``priors``."""
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(C=_C, max_iter=3000).fit(x, y, sample_weight=weights)
    classes = list(model.classes_)
    if priors == "balanced":
        return model, np.zeros(len(classes))
    trained = pd.Series(weights, index=y).groupby(level=0).sum()
    trained = trained / trained.sum()
    target = np.array([served_mix.get(c, 1e-4) for c in classes])
    return model, np.log(target) - np.log(np.array([trained[c] for c in classes]))


def probabilities(model, shift: np.ndarray, x: np.ndarray) -> np.ndarray:
    """What the shipped head computes: ``Head.probabilities`` with this model's weights and the
    shifted bias."""
    return role_family_classifier.softmax(model.decision_function(x) + shift)


def decide(p: np.ndarray, classes: list[str], cutoff: float) -> np.ndarray:
    """The serving decision, through the pipeline's own ``choose_families``."""
    return np.array(
        [
            family
            for family, _ in role_family_classifier.choose_families(p, classes, cutoff)
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--table", type=Path, required=True)
    ap.add_argument("--dev", type=Path, required=True)
    ap.add_argument("--test", type=Path)
    ap.add_argument("--version", type=int, required=True)
    ap.add_argument("--per-family", type=int, default=800)
    ap.add_argument("--min-coverage", type=float, default=0.90)
    ap.add_argument("--seed", type=int, default=20260928)
    args = ap.parse_args()

    from sklearn.model_selection import GroupKFold

    if (_HEAD_DIR / "manifest.json").exists():
        prior = json.loads((_HEAD_DIR / "manifest.json").read_text(encoding="utf-8"))[
            "version"
        ]
        if args.version <= prior:
            print(
                f"head version {prior} exists; a new head must bump --version past it",
                flush=True,
            )
            return 1

    silver, served_mix = silver_and_served_mix(
        served_rows(args.table), args.per_family, args.seed
    )
    dev = pd.read_parquet(args.dev).reset_index(drop=True)
    silver_title, dev_title = (
        _encode(silver.title.tolist()),
        _encode(dev.title.tolist()),
    )
    title_dim = silver_title.shape[1]
    silver_x = np.hstack([silver_title, row_vectors(args.table, silver.id.tolist())])
    dev_x = np.hstack([dev_title, row_vectors(args.table, dev.id.tolist())])
    silver_y, dev_y = silver.family.to_numpy(), dev.gold.to_numpy()
    folds = list(GroupKFold(n_splits=5).split(dev_x, dev_y, groups=dev.copy_key))

    best = None
    for w in _DEV_WEIGHTS:
        for priors in _PRIORS:
            out_of_fold = [None] * len(dev)
            for train_idx, val_idx in folds:
                x = np.vstack([silver_x, dev_x[train_idx]])
                y = np.concatenate([silver_y, dev_y[train_idx]])
                weights = np.concatenate(
                    [np.ones(len(silver_x)), np.full(len(train_idx), w)]
                )
                keep = weights > 0
                model, shift = fit(x[keep], y[keep], weights[keep], priors, served_mix)
                p = probabilities(model, shift, dev_x[val_idx])
                for i, row in zip(val_idx, p):
                    out_of_fold[i] = dict(zip(model.classes_, row))
            classes = sorted({c for row in out_of_fold for c in row})
            oof = np.array([[row.get(c, 0.0) for c in classes] for row in out_of_fold])
            accuracy = float((decide(oof, classes, 0.0) == dev_y).mean())
            print(
                f"w={w:>4} priors={priors:11s}: out-of-fold dev accuracy {accuracy:.3f} (n={len(dev)})",
                flush=True,
            )
            if best is None or accuracy > best[0]:
                best = (accuracy, w, priors, classes, oof)
    cv_accuracy, w, priors, classes, oof = best

    uniform = (dev.sample_source == "uniform").to_numpy()
    chosen = None
    for cutoff in _CUTOFF_GRID:
        decided = decide(oof[uniform], classes, cutoff)
        covered = decided != role_family_classifier.UNCLASSIFIED
        coverage = float(covered.mean())
        on_covered = (
            float((decided[covered] == dev_y[uniform][covered]).mean())
            if covered.any()
            else 0.0
        )
        if coverage >= args.min_coverage and (chosen is None or on_covered > chosen[1]):
            chosen = (cutoff, on_covered, coverage)
    cutoff, uniform_on_covered, uniform_coverage = chosen
    print(
        f"chosen: w={w}, priors={priors}, cutoff {cutoff} (uniform dev n={int(uniform.sum())}: "
        f"coverage {uniform_coverage:.3f}, accuracy on covered {uniform_on_covered:.3f})",
        flush=True,
    )

    x = np.vstack([silver_x, dev_x])
    y = np.concatenate([silver_y, dev_y])
    weights = np.concatenate([np.ones(len(silver_x)), np.full(len(dev_x), w)])
    keep = weights > 0
    model, shift = fit(x[keep], y[keep], weights[keep], priors, served_mix)
    classes = list(model.classes_)
    assert NON_TECH in classes, "the head must be able to say non-tech"
    if role_family_classifier.UNCLASSIFIED in classes:
        # a gold "unclassified-tech" row trained a class; the cutoff alone may produce that family
        drop = classes.index(role_family_classifier.UNCLASSIFIED)
        classes.pop(drop)
        model.coef_ = np.delete(model.coef_, drop, axis=0)
        model.intercept_ = np.delete(model.intercept_, drop)
        shift = np.delete(shift, drop)

    if args.test is not None:
        test = pd.read_parquet(args.test)
        test_x = np.hstack(
            [_encode(test.title.tolist()), row_vectors(args.table, test.id.tolist())]
        )
        p = probabilities(model, shift, test_x)
        decided, truth = decide(p, classes, cutoff), test.gold.to_numpy()
        covered = decided != role_family_classifier.UNCLASSIFIED
        print(
            f"TEST n={len(test)}: accuracy {float((decided == truth).mean()):.3f}, coverage "
            f"{float(covered.mean()):.3f}, accuracy on covered "
            f"{float((decided[covered] == truth[covered]).mean()):.3f}",
            flush=True,
        )

    _HEAD_DIR.mkdir(parents=True, exist_ok=True)
    np.savez(
        _HEAD_DIR / "head.npz",
        title_weights=model.coef_[:, :title_dim].astype(np.float32),
        row_weights=model.coef_[:, title_dim:].astype(np.float32),
        bias=(model.intercept_ + shift).astype(np.float32),
    )
    (_HEAD_DIR / "manifest.json").write_text(
        json.dumps(
            {
                "version": args.version,
                "model": _MODEL,
                "model_revision": _MODEL_REVISION,
                "row_vector": {
                    "column": "vector",
                    "model": ROW_VECTOR_MODEL,
                    "dim": int(silver_x.shape[1] - title_dim),
                },
                "families": classes,
                "cutoff": cutoff,
                "trained_on": {
                    "silver_titles": len(silver),
                    "per_family_cap": args.per_family,
                    "dev_rows": len(dev),
                    "dev_weight": w,
                    "priors": priors,
                    "C": _C,
                    "dev_out_of_fold_accuracy": round(cv_accuracy, 4),
                    "uniform_dev_coverage": round(uniform_coverage, 4),
                    "uniform_dev_accuracy_on_covered": round(uniform_on_covered, 4),
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {_HEAD_DIR}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

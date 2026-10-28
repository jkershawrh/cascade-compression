"""Train per-domain 4-class heads over a frozen sentence encoder.

Distillation trainer: takes teacher-labeled signal rows and emits head
artifacts (weights + contract) consumable by the sc-head server.

Domain-agnostic by construction: the domain list arrives as data (labeled
files named ``<domain>-labeled.jsonl``), the label space is the engine's
Cascade vocabulary, and every head is fitted the same way. Class imbalance
is handled with class weights; temperature is calibrated on the held-out
split so softmax confidence is meaningful to the engine's hybrid margin
gate.

Input row shape::

    {"signal": {...}, "text": "<serialized signal>", "teacher": "routine_noise"}

Output artifact (per domain, named ``cascade-<domain>.json``)::

    {"contract": {...}, "temperature": float, "classes": [...],
     "dim": int, "weights": [[...] x 4], "bias": [floats]}
"""
from __future__ import annotations

import glob
import json
import os
from collections import Counter

import numpy as np

CASCADE_LABELS = ["routine_noise", "known_pattern", "needs_attention", "real_incident"]
DEFAULT_ENCODER = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL_REVISION = "all-MiniLM-L6-v2@main+head-v1"


def fit_temperature(probs: np.ndarray, y_true: np.ndarray) -> float:
    """Pick T minimizing NLL of the temperature-scaled softmax."""
    best_t, best_nll = 1.0, float("inf")
    logp = np.log(np.clip(probs, 1e-9, 1))
    for t in np.arange(0.5, 3.01, 0.05):
        scaled = logp / t
        scaled -= scaled.max(axis=1, keepdims=True)
        p = np.exp(scaled)
        p /= p.sum(axis=1, keepdims=True)
        nll = -np.mean(np.log(np.clip(
            p[np.arange(len(y_true)), y_true], 1e-9, 1)))
        if nll < best_nll:
            best_nll, best_t = nll, float(t)
    return best_t


def reindex_probabilities(raw: np.ndarray, classes: np.ndarray,
                          n_labels: int = len(CASCADE_LABELS)) -> np.ndarray:
    """Expand a fitted classifier's probabilities into the full label space.

    A class may be absent from a small training split; its probability is
    set to a floor so the head still emits a valid distribution over all
    Cascade labels.
    """
    probs = np.full((len(raw), n_labels), 1e-9)
    for src_idx, cls in enumerate(classes):
        probs[:, int(cls)] = raw[:, src_idx]
    return probs


def head_weights(clf, dim: int, n_labels: int = len(CASCADE_LABELS)):
    """Map fitted rows into CASCADE_LABELS order.

    Binary fits return a single discriminator row (class 1 vs class 0);
    it expands to +/- rows for the two classes.
    """
    W = np.zeros((n_labels, dim))
    b = np.zeros(n_labels)
    if len(clf.classes_) == 2:
        c0, c1 = int(clf.classes_[0]), int(clf.classes_[1])
        W[c0] = -clf.coef_[0]
        W[c1] = clf.coef_[0]
        b[c0] = -clf.intercept_[0]
        b[c1] = clf.intercept_[0]
    else:
        for src_idx, cls in enumerate(clf.classes_):
            W[int(cls)] = clf.coef_[src_idx]
            b[int(cls)] = clf.intercept_[src_idx]
    return W, b


def load_labeled_rows(path: str) -> list[dict]:
    """Load teacher-labeled rows, tolerating a truncated tail line."""
    rows = []
    with open(path, errors="replace") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def train_domain(rows: list[dict], encoder, domain: str,
                 model_revision: str = DEFAULT_MODEL_REVISION,
                 test_size: float = 0.15, min_rows: int = 200) -> dict | None:
    """Fit one domain head; returns a report dict or None when untrainable."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import train_test_split

    rows = [r for r in rows if r.get("teacher") in CASCADE_LABELS]
    if len(rows) < min_rows:
        return {"domain": domain, "skipped": f"only {len(rows)} labels"}
    n_classes = len({r["teacher"] for r in rows})
    if n_classes < 2:
        return {"domain": domain,
                "skipped": "single class in labels - corpus lacks diversity"}

    texts = [r["text"] for r in rows]
    y = np.array([CASCADE_LABELS.index(r["teacher"]) for r in rows])
    X = encoder.encode(texts, normalize_embeddings=True,
                       convert_to_numpy=True, show_progress_bar=False)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=42, stratify=y)
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=1.0)
    clf.fit(X_train, y_train)

    raw = clf.predict_proba(X_test)
    probs = reindex_probabilities(raw, clf.classes_)
    pred = probs.argmax(axis=1)
    accuracy = float((pred == y_test).mean())
    temperature = fit_temperature(probs, y_test)
    margins = np.sort(probs, axis=1)
    margin_dist = margins[:, -1] - margins[:, -2]

    W, b = head_weights(clf, X.shape[1])
    signal = f"cascade-{domain}"
    contract = {
        "classifier_id": "cascade-signal-classifier",
        "signal": signal,
        "taxonomy_revision": f"{signal}-head-v1",
        "model_revision": model_revision,
        "tokenizer_revision": model_revision,
        "method": "classification-head",
        "labels": CASCADE_LABELS,
    }
    artifact = {
        "contract": contract,
        "temperature": temperature,
        "classes": CASCADE_LABELS,
        "dim": int(X.shape[1]),
        "weights": W.round(6).tolist(),
        "bias": b.round(6).tolist(),
    }
    return {
        "domain": domain, "n": len(rows), "held_out_n": len(y_test),
        "held_out_accuracy": round(accuracy, 4),
        "temperature": temperature,
        "margin_p50": round(float(np.percentile(margin_dist, 50)), 4),
        "label_distribution": dict(Counter(r["teacher"] for r in rows)),
        "artifact": artifact,
    }


def train_all(in_dir: str, out_dir: str, encoder=None) -> dict:
    """Train every ``<domain>-labeled.jsonl`` found in ``in_dir``."""
    from sentence_transformers import SentenceTransformer
    if encoder is None:
        encoder = SentenceTransformer(DEFAULT_ENCODER)
    os.makedirs(out_dir, exist_ok=True)
    report = {}
    for path in sorted(glob.glob(os.path.join(in_dir, "*-labeled.jsonl"))):
        domain = os.path.basename(path).replace("-labeled.jsonl", "")
        rows = load_labeled_rows(path)
        result = train_domain(rows, encoder, domain)
        if result is None or "skipped" in result:
            print(f"{domain}: {result['skipped'] if result else 'no rows'}")
            continue
        out = os.path.join(out_dir, f"cascade-{domain}.json")
        with open(out, "w") as f:
            json.dump(result.pop("artifact"), f)
        report[domain] = result
        print(f"{domain}: held-out acc {result['held_out_accuracy']:.3f} "
              f"| T={result['temperature']:.2f} "
              f"| margin p50={result['margin_p50']:.3f}")
    return report

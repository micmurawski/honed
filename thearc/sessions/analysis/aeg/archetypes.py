"""Step 5.6: session archetypes, cases clustered by how they flow.

Each case becomes a vector: normalised counts of the most common transitions, its phase mix, and its length and
token use (log). Clustering uses UMAP and HDBSCAN when they are installed (``umap-learn``, ``scikit-learn``), PCA and
HDBSCAN with scikit-learn alone, and otherwise numpy only: PCA by SVD and k-means++ with k chosen by silhouette.
Clusters are named by rules from what sets them apart; ``name`` can ask a model instead.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from .graph import CaseSequence

PHASES = ("EXPLORE", "PLAN", "EDIT", "TEST", "DEBUG", "VERIFY", "DELEGATE", "USER_TURN", "ASK_USER")


def _features(sequences: Sequence[CaseSequence], transitions: int) -> tuple[Any, list[str]]:
    import numpy as np

    counts = Counter(pair for s in sequences for pair in zip(s.names, s.names[1:], strict=False))
    vocabulary = [pair for pair, _ in counts.most_common(transitions)]
    index = {pair: i for i, pair in enumerate(vocabulary)}
    names = [f"{a} → {b}" for a, b in vocabulary] + [f"share:{p}" for p in PHASES] + ["log length", "log tokens"]
    rows = np.zeros((len(sequences), len(names)))
    for row, sequence in enumerate(sequences):
        pairs = list(zip(sequence.names, sequence.names[1:], strict=False))
        for pair in pairs:
            if pair in index:
                rows[row, index[pair]] += 1 / len(pairs)
        heads = Counter(state.name.split(" > ")[-1].split(":")[0] for state in sequence.states)
        total = sum(heads.values()) or 1
        for offset, phase in enumerate(PHASES):
            rows[row, len(vocabulary) + offset] = heads[phase] / total
        rows[row, -2] = math.log1p(len(sequence.states))
        rows[row, -1] = math.log1p(sequence.case.tokens)
    return rows, names


def _kmeans(points: Any, k: int, seed: int) -> Any:
    import numpy as np

    rng = np.random.default_rng(seed)
    centres = [points[rng.integers(len(points))]]
    for _ in range(1, k):  # k-means++ seeding.
        distance = np.min([((points - c) ** 2).sum(axis=1) for c in centres], axis=0)
        total = distance.sum()
        pick = rng.choice(len(points), p=distance / total) if total else rng.integers(len(points))
        centres.append(points[pick])
    centres_array = np.array(centres)
    labels = np.zeros(len(points), dtype=int)
    for _ in range(100):
        distances = ((points[:, None, :] - centres_array[None, :, :]) ** 2).sum(axis=2)
        new = distances.argmin(axis=1)
        if (new == labels).all() and _ > 0:
            break
        labels = new
        for cluster in range(k):
            if (labels == cluster).any():
                centres_array[cluster] = points[labels == cluster].mean(axis=0)
    return labels


def _silhouette(points: Any, labels: Any) -> float:
    import numpy as np

    distances = np.sqrt(((points[:, None, :] - points[None, :, :]) ** 2).sum(axis=2))
    scores = []
    for i, label in enumerate(labels):
        same = labels == label
        if same.sum() < 2:
            continue
        a = distances[i, same].sum() / (same.sum() - 1)
        b = min(distances[i, labels == other].mean() for other in set(labels.tolist()) if other != label)
        scores.append((b - a) / max(a, b) if max(a, b) else 0.0)
    return float(np.mean(scores)) if scores else -1.0


def cluster(points: Any, *, seed: int = 0) -> tuple[Any, str]:
    """Cluster labels (−1 for noise) and the method used."""
    import numpy as np

    size = max(5, len(points) // 50)
    try:
        from sklearn.cluster import HDBSCAN

        try:
            import umap

            embedded = umap.UMAP(n_components=min(5, len(points) - 2), random_state=seed).fit_transform(points)
            method = "UMAP + HDBSCAN"
        except ImportError:
            from sklearn.decomposition import PCA

            embedded = PCA(n_components=min(10, points.shape[1], len(points) - 1)).fit_transform(points)
            method = "PCA + HDBSCAN"
        return HDBSCAN(min_cluster_size=size).fit_predict(embedded), method
    except ImportError:
        pass
    centred = points - points.mean(axis=0)
    _, _, vectors = np.linalg.svd(centred, full_matrices=False)
    reduced = centred @ vectors[: min(10, vectors.shape[0])].T
    best, best_score = np.zeros(len(points), dtype=int), -1.0
    for k in range(2, min(8, len(points) // size) + 1):
        labels = _kmeans(reduced, k, seed)
        score = _silhouette(reduced, labels)
        if score > best_score:
            best, best_score = labels, score
    return best, f"PCA + k-means (silhouette {best_score:.2f})"


def archetypes(sequences: Sequence[CaseSequence], *, transitions: int = 60, seed: int = 0,
               name: Callable[[str], str] | None = None) -> dict[str, Any]:  # fmt: skip
    """Clusters of cases, each with its size, outcome, what sets it apart, and example cases.

    ``name(prompt) -> answer`` names a cluster from a summary of it (an LLM); without it, rules name it.
    """
    try:
        import numpy as np
    except ImportError:
        return {"method": None, "note": "archetypes need numpy (pip install 'thearc[aeg]')", "clusters": []}
    if len(sequences) < 10:
        return {"method": None, "note": f"too few cases to cluster ({len(sequences)}; 10 needed)", "clusters": []}
    rows, names = _features(sequences, transitions)
    spread = rows.std(axis=0)
    scaled = (rows - rows.mean(axis=0)) / np.where(spread > 0, spread, 1)
    labels, method = cluster(scaled, seed=seed)
    clusters = []
    baseline = sum(s.success for s in sequences) / len(sequences)
    for label in sorted(set(labels.tolist())):
        members = np.flatnonzero(labels == label)
        mean = scaled[members].mean(axis=0)
        distinct = [names[i] for i in np.argsort(-mean)[:4] if mean[i] > 0.3]
        centre = scaled[members].mean(axis=0)
        nearest = members[np.argsort(((scaled[members] - centre) ** 2).sum(axis=1))[:5]]
        chosen = [sequences[int(i)] for i in members]
        outcomes = Counter(s.outcome for s in chosen)
        card = {
            "cluster": int(label),
            "noise": label == -1,
            "cases": len(members),
            "completed": round(sum(s.success for s in chosen) / len(chosen), 3),
            "baseline_completed": round(baseline, 3),
            "outcomes": dict(outcomes.most_common()),
            "median_states": statistics.median(len(s.states) for s in chosen),
            "median_tokens": statistics.median(s.case.tokens for s in chosen),
            "distinguishing": distinct,
            "examples": [sequences[int(i)].case.case_id for i in nearest],
        }
        card["name"] = "unclustered" if label == -1 else _rule_name(card)
        if name is not None and label != -1:
            samples = "\n".join(" → ".join(sequences[int(i)].names[:30]) for i in nearest)
            prompt = (f"Name this cluster of coding-agent sessions in at most five words. Stats: {card}.\n"
                      f"Sample state sequences:\n{samples}\nAnswer with the name only.")  # fmt: skip
            try:
                card["name"] = name(prompt).strip().strip('"').splitlines()[0][:60] or card["name"]
            except (RuntimeError, OSError, ValueError, IndexError):
                pass
        clusters.append(card)
    clusters.sort(key=lambda card: (card["noise"], -card["cases"]))
    return {"method": method, "features": len(names), "clusters": clusters}


def _rule_name(card: dict[str, Any]) -> str:
    traits = [trait.replace("share:", "").replace("log length", "long").replace("log tokens", "token-heavy")
              for trait in card["distinguishing"][:2]]  # fmt: skip
    verdict = "mostly completed" if card["completed"] >= max(0.5, card["baseline_completed"]) else "often failing"
    return f"{' + '.join(traits) or 'typical'} · {verdict}"

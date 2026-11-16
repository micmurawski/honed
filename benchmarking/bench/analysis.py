"""Is `B` better than `A`? The paired, family-level analysis of benchmarking/README.md.

Each run record is ``{"task", "family", "arm": "A"|"B", "repetition", "passed": bool | None, "score": float}``;
``passed: None`` is a run that could not be scored (an infrastructure fault). The outcome is ``score`` (the fraction of
hidden tests passing, 0 to 1), or ``passed`` with ``outcome="passed"``. For a task, ``d`` is B's mean outcome minus
A's over the repetitions where both arms were scored; ``D[f]`` averages ``d`` over a family's tasks, and ``Delta``
averages ``D`` over families, each family weighing the same.

Families are the independent units, so the uncertainty comes from them: the interval resamples families (with all
their tasks and repetitions, which is resampling their ``D``), and with few families the sign-flip test on ``D`` is
exact.
"""

from __future__ import annotations

import itertools
import math
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist, fmean, stdev

Z = NormalDist()


def task_differences(records: Iterable[Mapping[str, object]], *, worst_case: bool = False,
                     outcome: str = "score") -> dict[str, tuple[str, float]]:
    """Each task's ``(family, d)``. A pair with an unscored run is left out, or with ``worst_case`` scored as B failing
    and A passing."""
    runs: dict[tuple[str, int], dict[str, float | None]] = defaultdict(dict)
    families: dict[str, str] = {}
    for record in records:
        task = str(record["task"])
        families[task] = str(record["family"])
        value = None if record["passed"] is None else float(record.get(outcome, record["passed"]))  # type: ignore[arg-type]
        runs[(task, int(str(record["repetition"])))][str(record["arm"])] = value
    differences: dict[str, list[float]] = defaultdict(list)
    for (task, _), arms in runs.items():
        a, b = arms.get("A"), arms.get("B")
        if a is None or b is None:
            if not worst_case:
                continue
            a = 1.0 if a is None else a
            b = 0.0 if b is None else b
        differences[task].append(b - a)
    return {task: (families[task], fmean(values)) for task, values in differences.items()}


def family_differences(records: Iterable[Mapping[str, object]], *, worst_case: bool = False,
                       outcome: str = "score") -> dict[str, float]:
    """Each family's ``D``: the mean ``d`` of its tasks."""
    by_family: dict[str, list[float]] = defaultdict(list)
    for family, d in task_differences(records, worst_case=worst_case, outcome=outcome).values():
        by_family[family].append(d)
    return {family: fmean(values) for family, values in sorted(by_family.items())}


def bootstrap_interval(values: Sequence[float], *, draws: int = 10_000, seed: int = 0,
                       level: float = 0.95) -> tuple[float, float]:
    """The percentile interval of the mean, resampling ``values`` (one per family) with replacement."""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(fmean(rng.choices(values, k=n)) for _ in range(draws))
    tail = (1 - level) / 2
    return means[int(tail * draws)], means[min(draws - 1, int((1 - tail) * draws))]


def sign_flip_p(values: Sequence[float], *, draws: int = 100_000, seed: int = 0) -> float:
    """Two-sided p-value of the mean under "A and B are exchangeable within each family": every family's sign flipped
    at random. Exact (all 2^n flips) up to 20 families, Monte Carlo beyond."""
    observed = abs(fmean(values))
    if len(values) <= 20:
        flips = list(itertools.product((1, -1), repeat=len(values)))
    else:
        rng = random.Random(seed)
        flips = [tuple(rng.choice((1, -1)) for _ in values) for _ in range(draws)]
    extreme = sum(abs(fmean([s * v for s, v in zip(signs, values, strict=True)])) >= observed - 1e-12
                  for signs in flips)
    return extreme / len(flips)


@dataclass(frozen=True)
class Result:
    families: int
    tasks: int
    delta: float  # B's mean outcome minus A's, family-weighted.
    interval: tuple[float, float]
    p: float  # Sign-flip test.
    verdict: str

    def __str__(self) -> str:
        low, high = self.interval
        return (f"Delta = {self.delta:+.1%} (95% interval {low:+.1%} to {high:+.1%}; sign-flip p = {self.p:.3f}) over "
                f"{self.families} families, {self.tasks} tasks: {self.verdict}")


def analyze(records: Sequence[Mapping[str, object]], *, seed: int = 0, worst_case: bool = False,
            outcome: str = "score") -> Result:
    """The verdict of the README: improvement or regression when the interval excludes 0 (and, with 20 families or
    fewer, the sign-flip test agrees at 5%), otherwise no visible difference."""
    families = family_differences(records, worst_case=worst_case, outcome=outcome)
    values = list(families.values())
    if len(values) < 2:
        raise ValueError(f"{len(values)} scored famil{'y' if len(values) == 1 else 'ies'}: nothing to compare")
    delta = fmean(values)
    interval = bootstrap_interval(values, seed=seed)
    p = sign_flip_p(values, seed=seed)
    separated = interval[0] > 0 or interval[1] < 0
    if separated and (len(values) > 20 or p < 0.05):
        verdict = "improvement" if delta > 0 else "regression"
    else:
        verdict = "no visible difference"
    tasks = len(task_differences(records, worst_case=worst_case, outcome=outcome))
    return Result(len(values), tasks, delta, interval, p, verdict)


def families_needed(sd: float, gain: float, *, smallest: float = 0.0, power: float = 0.8, alpha: float = 0.05) -> int:
    """Families for ``power`` to see a true gain of ``gain`` over ``smallest`` (two-sided ``alpha``), when family
    differences ``D`` spread with standard deviation ``sd`` (normal approximation)."""
    z = Z.inv_cdf(1 - alpha / 2) + Z.inv_cdf(power)
    return math.ceil((z * sd / (gain - smallest)) ** 2)


def spread(records: Sequence[Mapping[str, object]], outcome: str = "score") -> float:
    """The standard deviation of the family differences ``D``: what ``families_needed`` takes, from a pilot."""
    return stdev(family_differences(records, outcome=outcome).values())

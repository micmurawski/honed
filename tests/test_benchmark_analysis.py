"""The benchmark's paired, family-level analysis (benchmarking/bench/analysis.py)."""

import itertools

import pytest

from benchmarking.bench.analysis import (
    analyze,
    bootstrap_interval,
    families_needed,
    family_differences,
    sign_flip_p,
    task_differences,
)


def runs(task, family, a, b):
    """Records of one task: ``a`` and ``b`` are its pass/fail per repetition."""
    return [{"task": task, "family": family, "arm": arm, "repetition": r, "passed": passed}
            for arm, outcomes in (("A", a), ("B", b)) for r, passed in enumerate(outcomes)]


def test_tasks_average_their_repetitions_and_families_their_tasks():
    records = [*runs("t1", "f1", [0, 0, 1], [1, 1, 1]),   # d = 2/3
               *runs("t2", "f1", [1, 1, 1], [1, 1, 1]),   # d = 0
               *runs("t3", "f2", [1, 0, 0], [0, 0, 0])]   # d = -1/3
    assert task_differences(records) == {"t1": ("f1", pytest.approx(2 / 3)), "t2": ("f1", 0.0),
                                         "t3": ("f2", pytest.approx(-1 / 3))}
    assert family_differences(records) == {"f1": pytest.approx(1 / 3), "f2": pytest.approx(-1 / 3)}


def test_a_family_of_many_tasks_weighs_as_much_as_one_of_a_single_task():
    records = [*itertools.chain.from_iterable(runs(f"big{i}", "big", [0], [1]) for i in range(9)),
               *runs("small", "small", [1], [0])]
    assert analyze(records).delta == pytest.approx(0.0)


def test_unscored_pairs_are_dropped_or_count_against_b():
    records = runs("t1", "f1", [1, None], [1, 1])
    assert task_differences(records)["t1"][1] == 0.0  # Only the scored pair.
    assert task_differences(records, worst_case=True)["t1"][1] == 0.0  # A counted passing, B as it was: 1 - 1.
    records = runs("t1", "f1", [0, 1], [1, None])
    assert task_differences(records)["t1"][1] == 1.0
    assert task_differences(records, worst_case=True)["t1"][1] == pytest.approx(0.0)  # (1 + -1) / 2


def test_the_outcome_is_the_score_of_the_hidden_tests_when_runs_have_one():
    records = [{"task": "t", "family": "f", "arm": arm, "repetition": 0, "passed": False, "score": score}
               for arm, score in (("A", 0.25), ("B", 0.75))]
    assert task_differences(records)["t"][1] == pytest.approx(0.5)  # Neither passed every test: B passed more.
    assert task_differences(records, outcome="passed")["t"][1] == 0.0


def test_the_sign_flip_test_is_exact_for_few_families():
    assert sign_flip_p([1.0, 1.0, 1.0]) == pytest.approx(2 / 8)  # All + or all -.
    assert sign_flip_p([0.5, -0.5]) == 1.0
    assert sign_flip_p([1.0] * 6) == pytest.approx(2 / 64)


def test_the_interval_resamples_families_and_is_reproducible():
    values = [0.1, 0.2, 0.0, 0.3, 0.1, 0.2]
    low, high = bootstrap_interval(values, seed=1)
    assert low <= sum(values) / len(values) <= high and (low, high) == bootstrap_interval(values, seed=1)
    assert bootstrap_interval([0.2] * 5) == (pytest.approx(0.2), pytest.approx(0.2))


def test_verdicts():
    gains = [r for i in range(25) for r in runs(f"t{i}", f"f{i}", [0, 0, 1], [1, 1, 1])]
    assert analyze(gains).verdict == "improvement"
    losses = [r for i in range(25) for r in runs(f"t{i}", f"f{i}", [1, 1, 1], [0, 1, 0])]
    assert analyze(losses).verdict == "regression"
    noise = [r for i in range(40) for r in runs(f"t{i}", f"f{i}", *(([0, 1], [1, 1]) if i % 2 else ([1, 1], [0, 1])))]
    result = analyze(noise)
    assert result.verdict == "no visible difference" and result.interval[0] < 0 < result.interval[1]
    few = [r for i in range(4) for r in runs(f"t{i}", f"f{i}", [0], [1])]  # Interval [1, 1], but p = 2/16.
    assert analyze(few).verdict == "no visible difference"


def test_the_sample_size_of_the_readme():
    assert families_needed(0.3, 0.10) == 71
    assert families_needed(0.3, 0.188) == 20

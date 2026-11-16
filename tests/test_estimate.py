"""A round's cost, estimated before it runs: from the size of its sessions and what earlier runs cost."""

from types import SimpleNamespace

from thearc.evolve.estimate import estimate
from thearc.sessions.export import export_events

from .sessions.test_round_trip import SESSION


def journal(reflections=(), curations=()):
    return SimpleNamespace(journal=SimpleNamespace(
        reflections=[SimpleNamespace(evidence=evidence, reflector={"usage": {"cost": cost}}) for evidence, cost in
                     reflections],
        entries=[SimpleNamespace(extra={"curation": name, "usage": {"cost": cost}}) for name, cost in curations]))


def test_a_first_round_gets_its_size_only():
    found = estimate([SESSION, SESSION], "codex", journal())
    size = len(export_events(SESSION.selected_events, "codex", strict=False).text) // 4
    assert (found.sessions, found.tokens, found.cost) == (2, 2 * size, None)
    assert str(found).startswith("2 session(s), ~") and "no cost history yet" in str(found)


def test_later_rounds_cost_what_earlier_evidence_cost():
    size = len(export_events(SESSION.selected_events, "claude", strict=False).text) // 4
    history = journal([({"tokens": 1000}, 0.10), ({"tokens": 3000}, 0.30)],
                      [("c1", 0.50), ("c1", 0.50), ("c2", 0.70)])  # A curation's entries share its cost: once each.
    found = estimate([SESSION], "claude", history)
    assert abs(found.cost - (size * 0.0001 + 0.60)) < 1e-9
    assert found.basis == "from 2 earlier reflection(s) and 2 curation(s)" and f"${found.cost:.2f}" in str(found)


def test_reflections_without_their_size_count_by_the_run():
    found = estimate([SESSION, SESSION], "claude", journal([({}, 0.20), ({}, 0.40)]))
    assert abs(found.cost - 0.60) < 1e-9 and found.basis.endswith("curation not included")

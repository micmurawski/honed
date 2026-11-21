"""``@node``: functions as flow nodes, with live objects passed through shared as they are."""

from dataclasses import dataclass

import pytest

from honed.flow import Flow
from honed.flow.decorators import node


class Live:  # Neither JSON nor pydantic: a session, a worktree, an open resource.
    def __init__(self, n):
        self.n = n


@dataclass(frozen=True)
class Box:  # A dataclass, which validation would rebuild.
    live: Live


def test_unvalidated_nodes_pass_live_objects_through_shared_as_they_are():
    live = Live(1)

    @node(validate=False)
    def start(live: Live):
        return {"box": Box(live), "count": live.n}, "go"

    @node(validate=False)
    def finish(box: Box, count: int):
        return {"same": box.live is live, "count": count + 1}

    start - "go" >> finish
    shared = {"live": live}
    Flow(start=start).run(shared)
    assert shared["same"] is True and shared["count"] == 2 and shared["box"].live is live


def test_a_batch_node_takes_each_item_as_a_parameter():
    seen = []

    @node(batch=True, items_key="todo", item="each", validate=False)
    def work(each: Live, factor: int, note: str = "-"):
        seen.append((each.n * factor, note))

    shared = {"todo": [Live(1), Live(2)], "factor": 10}
    Flow(start=work).run(shared)
    assert seen == [(10, "-"), (20, "-")]


def test_a_validated_batch_node_with_items_checks_them():
    @node(batch=True, items_key="todo", item="each")
    def double(each: int):
        return each * 2

    shared = {"todo": [1, "2"]}
    Flow(start=double).run(shared)
    assert shared["results"] == [2, 4]  # Coerced by validation.


def test_none_in_shared_is_a_value():
    @node(validate=False)
    def show(value: Live | None):
        return {"got": value}

    shared = {"value": None}
    Flow(start=show).run(shared)
    assert shared["got"] is None


def test_the_same_nodes_run_and_are_tracked_again(tmp_path):
    from honed.flow.viz import FlowTracker

    runs = []

    @node(validate=False)
    def first(n: int):
        return {"n": n + 1}, "next"

    @node(batch=True, items_key="items", item="each", validate=False)
    def second(each: int, n: int):
        runs.append(each * n)

    first - "next" >> second
    classes = (type(first), type(second))
    for round_ in (1, 2):
        page = tmp_path / f"round{round_}.html"
        FlowTracker(Flow(start=first), output=str(page)).run({"n": 1, "items": [1, 2]})
        assert (type(first), type(second)) == classes  # Untracked again once the run ends.
        assert page.read_text().count('class="chip success">success') == 2
    assert runs == [2, 4, 2, 4]


def test_a_failed_run_leaves_its_nodes_untracked(tmp_path):
    from honed.flow.viz import FlowTracker

    @node(validate=False)
    def boom(n: int):
        raise RuntimeError("no")

    cls = type(boom)
    with pytest.raises(RuntimeError):
        FlowTracker(Flow(start=boom), output=str(tmp_path / "p.html")).run({"n": 1})
    assert type(boom) is cls


def test_parameters_still_need_annotations():
    with pytest.raises(TypeError, match="no type annotation"):
        @node(validate=False)
        def untyped(value):
            return None

"""Token usage: what each harness reports for a run, recorded with reflections, curations and merges, and summed in
the evolution dataset once per run."""

import json
from types import SimpleNamespace

import pytest

from thearc.reflection import Reflector, Usage, runtimes
from thearc.resources.evolution import evolution

from .test_reflection import GOOD, SCHEMA, Scripted, answer
from .test_report import curated


def test_usage_adds_up_and_keeps_an_unreported_cost_out():
    total = Usage(input=10, output=5, cache_read=100) + Usage(input=1, output=1, cache_write=20, cost=0.5)
    assert total == Usage(input=11, output=6, cache_read=100, cache_write=20, cost=0.5) and total.total == 137
    assert Usage(input=3).to_dict() == {"input": 3, "output": 0, "cache_read": 0, "cache_write": 0, "reasoning": 0}
    assert Usage.from_dict({**total.to_dict(), "unknown": 1}) == total and not Usage()


def test_claude_reports_its_usage_and_cost(monkeypatch, tmp_path):
    result = {"subtype": "success", "result": "ok", "session_id": "s1", "total_cost_usd": 0.25,
              "usage": {"input_tokens": 12, "output_tokens": 30, "cache_read_input_tokens": 400,
                        "cache_creation_input_tokens": 50}}
    monkeypatch.setattr(runtimes, "_run", lambda *args, **kwargs: json.dumps(result))
    talk = runtimes._ClaudeConversation(runtimes.ClaudeRuntime(), tmp_path, runtimes.ToolEndpoint("http://x"), None,
                                        False)
    talk.send("one")
    talk.send("two")
    assert talk.usage == Usage(input=24, output=60, cache_read=800, cache_write=100, cost=0.5)


def test_pi_reports_usage_per_model_call(monkeypatch, tmp_path):
    def message(text, usage):
        return json.dumps({"type": "message_end", "message": {"role": "assistant", "usage": usage,
                                                               "content": [{"type": "text", "text": text}]}})

    calls = [message("thinking", {"input": 10, "output": 2, "cacheRead": 5, "cacheWrite": 0, "cost": {"total": 0.1}}),
             message("done", {"input": 3, "output": 4, "cacheRead": 7, "cacheWrite": 1, "cost": {"total": 0.2}})]
    monkeypatch.setattr(runtimes, "_run", lambda *args, **kwargs: "\n".join(calls))
    talk = runtimes._PiConversation(runtimes.PiRuntime(), tmp_path, runtimes.ToolEndpoint("http://x"),
                                    tmp_path / "submit.ts", None, tmp_path)
    assert talk.send("go") == "done"
    assert talk.usage.total == 13 + 6 + 12 + 1 and talk.usage.cost == pytest.approx(0.3)


def test_codex_reports_the_thread_total_so_each_turn_counts_the_difference():
    def breakdown(input_, cached, output):
        return SimpleNamespace(input_tokens=input_, cached_input_tokens=cached, output_tokens=output,
                               cache_write_input_tokens=0, reasoning_output_tokens=1, total_tokens=input_ + output)

    totals = [breakdown(100, 40, 10), breakdown(250, 140, 30)]

    class Thread:
        id = "t1"

        def run(self, message, **kwargs):
            return SimpleNamespace(status=SimpleNamespace(value="completed"), final_response="ok",
                                   usage=SimpleNamespace(total=totals.pop(0)))

    talk = runtimes._CodexConversation(Thread(), None, 60)
    talk.send("one")
    assert talk.usage == Usage(input=60, output=10, cache_read=40, reasoning=1)
    talk.send("two")
    assert talk.usage == Usage(input=110, output=30, cache_read=140, reasoning=1)


def test_the_dataset_counts_each_agent_run_once(agent, tmp_path):

    workspace = agent.init_workspace(tmp_path / "ws")
    runtime = Scripted(answer(GOOD), usage=Usage(input=100, output=20, cost=0.1))
    reflection = Reflector(runtime=runtime).reflect("Review.", workspace, SCHEMA)
    assert reflection.reflector["usage"] == {"input": 100, "output": 20, "cache_read": 0, "cache_write": 0,
                                             "reasoning": 0, "cost": 0.1}
    workspace.add_reflection(reflection)
    curation = curated(workspace, reflection, usage=Usage(input=1000, output=300, cache_read=5000))
    assert len(curation.entries) == 2 and all(entry.extra["usage"]["input"] == 1000 for entry in curation.entries)
    assert curation.curator["usage"]["cache_read"] == 5000

    usage = evolution(workspace.workspace)["usage"]  # Oldest first, but both fall in the same second here.
    assert sorted((run["kind"], run["id"], run["usage"]["input"]) for run in usage) == [
        ("curation", curation.id, 1000), ("reflection", reflection.id, 100)]  # Two entries, one curation.
    (curated_run,) = [run for run in usage if run["kind"] == "curation"]
    assert curated_run["harness"] == "codex" and curated_run["model"] == "scripted"

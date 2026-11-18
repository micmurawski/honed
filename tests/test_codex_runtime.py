import json

import pytest

from thearc.reflection import ReflectionError, runtimes
from thearc.reflection.runtimes import CodexRuntime, ToolEndpoint
from thearc.sessions import InstalledSession


def turn(text, thread="t1"):
    usage = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 2}
    return "\n".join(json.dumps(event) for event in (
        {"type": "thread.started", "thread_id": thread},
        {"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": text}},
        {"type": "turn.completed", "usage": usage}))


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(command, *, cwd, timeout, stdin=None, env_overrides=None):
        seen.append({"command": command, "cwd": cwd, "stdin": stdin})
        return turn(f"answer {len(seen)}", thread="forked" if "fork" in command else "t1")

    monkeypatch.setattr(runtimes, "_run", fake_run)
    return seen


def overrides(command):
    return [command[i + 1] for i, part in enumerate(command) if part == "-c"]


def test_a_reflection_starts_a_thread_then_resumes_it_with_its_tool_and_a_read_only_sandbox(calls, tmp_path):
    runtime = CodexRuntime(model="gpt-x", effort="high")
    with runtime.conversation(tmp_path, ToolEndpoint("http://127.0.0.1/mcp")) as talk:
        assert talk.send("Review this session") == "answer 1" and talk.id == "t1"
        assert talk.send("Try again") == "answer 2"
    first, second = calls[0]["command"], calls[1]["command"]
    assert first[:4] == ["codex", "exec", "-", "--json"] and second[:5] == ["codex", "exec", "resume", "t1", "-"]
    assert calls[0]["stdin"] == "Review this session" and calls[0]["cwd"] == tmp_path
    for command in (first, second):
        settings = overrides(command)
        assert 'mcp_servers.thearc.url="http://127.0.0.1/mcp"' in settings
        assert 'mcp_servers.thearc.default_tools_approval_mode="approve"' in settings
        assert 'sandbox_mode="read-only"' in settings and 'approval_policy="never"' in settings
        assert 'model_reasoning_effort="high"' in settings and 'web_search="disabled"' in settings
        assert command[command.index("-m") + 1] == "gpt-x"


def test_a_handoff_forks_the_installed_session_and_an_edit_may_write(calls, tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("")
    fork = InstalledSession("codex", "parent-id", rollout, command="")
    with CodexRuntime().conversation(tmp_path, ToolEndpoint("http://x"), fork=fork, edit=True) as talk:
        talk.send("Reflect")
        talk.send("Again")
    assert calls[0]["command"][:4] == ["codex", "exec", "fork", "parent-id"]
    assert calls[1]["command"][:4] == ["codex", "exec", "resume", "forked"]
    assert 'sandbox_mode="workspace-write"' in overrides(calls[0]["command"])
    assert "-m" not in calls[0]["command"]


def test_a_missing_handed_off_session_or_a_failed_turn_is_an_error(monkeypatch, tmp_path):
    fork = InstalledSession("codex", "gone", tmp_path / "missing.jsonl", command="")
    with (pytest.raises(ReflectionError, match="session not found") as error,
          CodexRuntime().conversation(tmp_path, ToolEndpoint("http://x"), fork=fork)):
        pass
    assert error.value.code == "missing_history"
    failed = "\n".join(json.dumps(event) for event in (
        {"type": "thread.started", "thread_id": "t1"}, {"type": "error", "message": "Reconnecting... 1/5"},
        {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}))
    monkeypatch.setattr(runtimes, "_run", lambda *args, **kwargs: failed)
    with (CodexRuntime().conversation(tmp_path, ToolEndpoint("http://x")) as talk,
          pytest.raises(ReflectionError, match="usage limit") as error):
        talk.send("Review")
    assert error.value.code == "incomplete" and talk.id == "t1"

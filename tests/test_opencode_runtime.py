import json
from pathlib import Path

from honed.reflection import runtimes
from honed.reflection.runtimes import OpenCodeRuntime, ToolEndpoint, _opencode_usage


def test_opencode_runtime_is_registered_for_evolve_and_reflection():
    from honed.evolve.setup import RUNTIMES
    from honed.reflection.runtimes import RUNTIMES as REFLECTION_RUNTIMES

    assert "opencode" in RUNTIMES
    assert REFLECTION_RUNTIMES["opencode"] is OpenCodeRuntime


def test_opencode_usage_maps_tokens_and_cost():
    usage = _opencode_usage(
        {"input": 100, "output": 30, "reasoning": 10, "cache": {"read": 20, "write": 5}}, 0.02
    )

    assert usage.to_dict() == {
        "input": 100, "output": 30, "reasoning": 10, "cache_read": 20, "cache_write": 5, "cost": 0.02
    }


def test_opencode_conversation_uses_isolated_mcp_and_permission_config(tmp_path, monkeypatch):
    observed = {}

    def fake_run(command, *, cwd, timeout, stdin=None, env_overrides=None):
        if command[1:3] == ["run", "--format"]:
            observed["command"] = command
            observed["config"] = json.loads(Path(env_overrides["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))
        return json.dumps({"type": "text", "sessionID": "ses_example", "part": {"text": "Done"}})

    monkeypatch.setattr(runtimes, "_run", fake_run)
    runtime = OpenCodeRuntime(model="provider/model", effort="high")
    with runtime.conversation(tmp_path, ToolEndpoint("http://127.0.0.1/mcp")) as conversation:
        assert conversation.send("Review this session") == "Done"
        assert conversation.id == "ses_example"
    config = observed["config"]
    assert config["mcp"]["servers"]["honed"]["url"] == "http://127.0.0.1/mcp"
    assert config["mcp"]["servers"]["honed"]["codemode"] is False
    rules = {(rule["action"], rule["effect"]) for rule in config["permissions"]}
    assert ("shell", "deny") in rules
    assert ("edit", "deny") in rules
    assert ("honed_submit_reflection", "allow") in rules
    assert "provider/model#high" in observed["command"]

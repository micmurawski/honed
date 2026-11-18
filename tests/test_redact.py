"""Redaction: secrets and personal paths out of a session before a reflector reads it; what the harness needs, kept."""

import json
from collections import Counter
from pathlib import Path

import pytest

from thearc.reflection import ReflectionError, Reflector
from thearc.reflection import runtimes as runtime_module
from thearc.sessions.redact import Redaction, summary

from .sessions.test_round_trip import SESSION
from .test_reflection import GOOD, SCHEMA, Scripted, answer

GITHUB = "ghp_" + "a1B2" * 9
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----"


@pytest.mark.parametrize(("secret", "kind"), [
    (GITHUB, "github-token"),
    (AWS, "aws-key"),
    ("sk-ant-api03-" + "x" * 30, "anthropic-key"),
    ("sk-proj-" + "Ab1" * 12, "openai-key"),
    ("xoxb-1234567890-abcdefghij", "slack-token"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", "jwt"),
    (KEY, "private-key"),
])
def test_known_secrets_are_replaced_with_their_kind(secret, kind):
    counts = Counter()
    text = Redaction(environment=False).text(f"found {secret} in the log", counts)
    assert text == f"found [REDACTED:{kind}] in the log" and counts == {kind: 1}


def test_assigned_secrets_lose_their_value_but_code_keeps_its_names():
    redaction = Redaction(environment=False)
    assert redaction.text('DB_PASSWORD=hunter2!x9') == "DB_PASSWORD=[REDACTED:assignment]"
    assert redaction.text('"api_key": "Zx81-ab9Q-77pp"') == '"api_key": "[REDACTED:assignment]"'
    assert redaction.text("https://bob:s3cr3t@example.com/x") == "https://[REDACTED:url-credentials]@example.com/x"
    for code in ("token = self.load_token()", "max_tokens: 100000000", "password = os.environ", "TOKEN=$GH_TOKEN"):
        assert redaction.text(code) == code


def test_environment_secrets_and_your_own_strings_are_replaced(monkeypatch):
    monkeypatch.setenv("MY_SERVICE_TOKEN", "plainlookingvalue")
    monkeypatch.setenv("EDITOR", "nano-editor-value")
    text = Redaction(literals=("Project Falcon",), patterns=(r"ACME-\d+",)).text(
        "plainlookingvalue for Project Falcon, ticket ACME-42, nano-editor-value")
    assert text == "[REDACTED:literal] for [REDACTED:literal], ticket [REDACTED:pattern], nano-editor-value"


def test_home_directories_become_anonymous(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/alice")))
    text = Redaction().text("opened /Users/alice/code/app.py and /home/bob/notes, not /Users/Shared/x")
    assert text == "opened ~/code/app.py and /home/user/notes, not /Users/Shared/x"


def test_an_export_stays_valid_and_keeps_what_the_harness_needs(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/alice")))
    records = [{"cwd": "/Users/alice/app", "message": {"content": f"export GITHUB_TOKEN={GITHUB}"}},
               {"cwd": "/Users/alice/app", "message": {"content": ["read /Users/alice/app/x.py"]}}]
    text = "".join(json.dumps(record) + "\n" for record in records)
    redacted, counts = Redaction(environment=False).export(text)
    first, second = (json.loads(line) for line in redacted.splitlines())
    assert first["cwd"] == "/Users/alice/app"  # Claude runs the fork there.
    assert first["message"]["content"] == "export GITHUB_TOKEN=[REDACTED:github-token]"
    assert second["message"]["content"] == ["read ~/app/x.py"] and redacted.endswith("\n")
    assert summary(counts) == "1 github-token, 1 home"
    assert Redaction.off().export(text) == (text, {})


def test_records_holding_unicode_line_separators_stay_whole():
    records = [{"message": "a\u2028b\x0bc\x1cd /Users/alice/x"}, {"message": "next"}]
    text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    redacted, _ = Redaction(environment=False).export(text)
    assert [json.loads(line)["message"].split(" ")[0] for line in redacted.split("\n") if line] == [
        "a\u2028b\x0bc\x1cd", "next"]


def test_the_reflector_reads_a_redacted_copy_and_says_so(agent, tmp_path):
    seen = {}

    class Reading(Scripted):  # Reads the installed copy, as the forked harness would.
        def conversation(self, workspace, tool, *, fork=None):
            seen["copy"] = Path(fork.path).read_text()
            return super().conversation(workspace, tool, fork=fork)

    session = SESSION.trajectories[0]  # Its user asks to "Run the tests"; here that counts as a secret.
    redaction = Redaction(literals=("Run the tests",))
    reflection = Reflector(runtime=Reading(answer(GOOD)), home=tmp_path, redaction=redaction).handoff(
        "Review.", session, agent, SCHEMA)
    assert "Run the tests" not in seen["copy"] and "[REDACTED:literal]" in seen["copy"]
    assert reflection.evidence["redacted"] == {"literal": 1} and reflection.evidence["tokens"] > 0
    assert any(note.startswith("redacted before the reflector read it: ") for note in reflection.notes)

    kept = Reflector(runtime=Reading(answer(GOOD)), home=tmp_path, redaction=Redaction.off()).handoff(
        "Review.", session, agent, SCHEMA)
    assert "Run the tests" in seen["copy"] and "redacted" not in kept.evidence


def test_attached_evidence_is_redacted_too(agent):
    runtime = Scripted(answer(GOOD))
    reflection = Reflector(runtime=runtime).reflect("Review.", agent, SCHEMA, evidence=f"pushed with {GITHUB}")
    assert GITHUB not in runtime.sent[0] and "[REDACTED:github-token]" in runtime.sent[0]
    assert reflection.evidence["redacted"] == {"github-token": 1}


def test_a_stalled_turn_times_out(tmp_path):
    with pytest.raises(ReflectionError, match="did not finish within 0s") as error:
        runtime_module._run(["sleep", "5"], cwd=tmp_path, timeout=0.05)
    assert error.value.code == "timeout"
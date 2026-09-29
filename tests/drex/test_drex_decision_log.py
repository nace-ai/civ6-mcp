"""Decision log: append-only JSONL, run identity, credentials never written."""

import json

from civ_mcp.drex.decision_log import DecisionLog, redact

SECRET = "apikey_22222222222222222222_secretsecretsecret"


def test_redact_removes_secret_substrings_and_auth_fields():
    data = {
        "headers": {"Authorization": f"Bearer {SECRET}", "Accept": "json"},
        "note": f"key was {SECRET}!",
        "nested": [{"api_key": SECRET}, SECRET],
        "DREX_API_KEY": SECRET,
    }
    clean = redact(data, [SECRET])
    text = json.dumps(clean)
    assert SECRET not in text
    assert "Authorization" not in clean["headers"]
    assert clean["note"] == "key was [REDACTED]!"
    assert "DREX_API_KEY" not in clean


def test_log_writes_sequenced_records_with_run_id(tmp_path):
    log = DecisionLog(tmp_path / "run.jsonl", run_id="drex-test", secrets=[SECRET])
    log.write("header", {"model": "drex-latest"})
    log.write("decision", {"echo": SECRET, "candidate_id": "research:X"})
    lines = (tmp_path / "run.jsonl").read_text().splitlines()
    records = [json.loads(line) for line in lines]
    assert [r["type"] for r in records] == ["header", "decision"]
    assert [r["seq"] for r in records] == [1, 2]
    assert all(r["run_id"] == "drex-test" for r in records)
    assert SECRET not in "\n".join(lines)


def test_short_secrets_are_ignored_to_avoid_mangling(tmp_path):
    assert redact({"a": "abc"}, ["ab"]) == {"a": "abc"}

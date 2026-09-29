"""civ-drex CLI: offline replay needs neither API access nor a running game."""

import json

import drex_fixtures as fx
import pytest

from civ_mcp import lua as lq
from civ_mcp.drex.cli import main
from civ_mcp.drex.observation import CoreObservation, DecisionInputs
from civ_mcp.drex.serialize import to_jsonable


def _fixture(tmp_path, category="research", entity="empire", inputs=None):
    core = CoreObservation(
        version="rome:42:T5:7",
        civ="rome",
        seed=42,
        local_player_id=fx.ME,
        overview=fx.overview(),
        tech=fx.tech_status(),
        progress=lq.ProgressTypes(None, "CIVIC_CODE_OF_LAWS"),
        cities=[fx.capital()],
        units=[fx.warrior()],
    )
    path = tmp_path / "fixture.json"
    path.write_text(
        json.dumps(
            {
                "decision_id": "T5#0007",
                "objective": "Grow.",
                "spec": {"category": category, "entity": entity},
                "core": to_jsonable(core),
                "inputs": to_jsonable(inputs or DecisionInputs()),
            }
        )
    )
    return path


def _answer(tmp_path, probs, choice):
    path = tmp_path / "answer.json"
    path.write_text(
        json.dumps(
            {
                "model": "drex-1.1",
                "answers": {
                    "decision": {
                        "type": "choice",
                        "choice": choice,
                        "probabilities": probs,
                        "confidence": 0.3,
                    }
                },
                "usage": {"input_tokens": 90, "output_tokens": 3},
            }
        )
    )
    return path


def test_replay_resolves_recorded_answer_to_planned_dispatch(tmp_path, capsys):
    fixture = _fixture(tmp_path)
    answer = _answer(
        tmp_path, {"Pottery": 0.2, "Mining": 0.7, "Animal Husbandry": 0.1}, "Mining"
    )
    assert main(["replay", "--fixture", str(fixture), "--answer", str(answer)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"]["candidate_id"] == "research:TECHNOLOGY_MINING"
    assert out["dispatch"] == {"method": "set_research", "args": ["TECHNOLOGY_MINING"]}
    assert out["point"]["observation_version"] == "rome:42:T5:7"


def test_replay_refuses_malformed_answer(tmp_path, capsys):
    fixture = _fixture(tmp_path)
    answer = _answer(tmp_path, {"Pottery": 0.5, "Writing": 0.5}, "Writing")
    assert main(["replay", "--fixture", str(fixture), "--answer", str(answer)]) == 4
    out = json.loads(capsys.readouterr().out)
    assert out["dispatch"] is None and "rejected" in out


def test_replay_with_random_baseline_is_labeled(tmp_path, capsys):
    fixture = _fixture(tmp_path)
    assert (
        main(
            [
                "replay",
                "--fixture",
                str(fixture),
                "--selector",
                "random-baseline",
                "--seed",
                "3",
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["decision"]["selector"] == "random-baseline"


def test_replay_preview_lists_options_without_selecting(tmp_path, capsys):
    inputs = DecisionInputs(
        unit=fx.warrior(),
        action_space=fx.warrior_space(),
        nearby_tiles=fx.tiles_around_warrior(),
    )
    fixture = _fixture(
        tmp_path, category="unit", entity=f"unit:{fx.WARRIOR_ID}", inputs=inputs
    )
    assert main(["replay", "--fixture", str(fixture)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] is None
    assert "Skip turn (stay here)" in out["request"]["options"]
    assert {e["option"] for e in out["point"]["exclusions"]} >= {"move to (10,11)"}


def test_verify_api_without_key_fails_cleanly(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("DREX_API_KEY", raising=False)
    code = main(["verify-api", "--env-file", str(tmp_path / "missing.env")])
    assert code == 2
    assert "DREX_API_KEY" in capsys.readouterr().err


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    text = capsys.readouterr().out
    for cmd in ("verify-api", "play", "dry-run", "replay", "probe"):
        assert cmd in text

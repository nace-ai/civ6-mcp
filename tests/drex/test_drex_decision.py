"""Decision validation: a model answer can only ever select a stored candidate."""

import math

import pytest

from civ_mcp.drex.candidates import (
    ActionKind,
    Candidate,
    DecisionCategory,
    DecisionPoint,
    MoveParams,
    ResearchParams,
    UnitRef,
)
from civ_mcp.drex.decision import (
    ChoiceAnswer,
    MalformedChoice,
    StaleDecision,
    UnknownCandidate,
    ensure_current,
    forced_decision,
    resolve_choice,
    validate_selection,
)


def _research(tech: str, name: str) -> Candidate:
    return Candidate.create(
        ActionKind.SET_RESEARCH,
        ResearchParams(tech_type=tech),
        label=name,
        facts={"turns": 5},
    )


POTTERY = ("TECHNOLOGY_POTTERY", "Pottery")
MINING = ("TECHNOLOGY_MINING", "Mining")
ARCHERY = ("TECHNOLOGY_ARCHERY", "Archery")


def _point(candidates, version: str = "rome:42:T5:1") -> DecisionPoint:
    return DecisionPoint.create(
        decision_id="T5#1",
        category=DecisionCategory.RESEARCH,
        entity="empire",
        observation_version=version,
        question="Which technology should the empire research next?",
        candidates=candidates,
        context={"turn": 5},
    )


def _three(order=(POTTERY, MINING, ARCHERY)) -> DecisionPoint:
    return _point([_research(t, n) for t, n in order])


def _answer(pairs, choice=None, confidence=0.5) -> ChoiceAnswer:
    return ChoiceAnswer(
        probabilities=tuple(pairs), choice=choice, confidence=confidence
    )


class TestCandidateIdentity:
    def test_id_is_derived_from_action_content(self):
        assert _research(*POTTERY).candidate_id == "research:TECHNOLOGY_POTTERY"

    def test_move_id_uses_composite_unit_id_and_destination(self):
        unit = UnitRef(unit_id=131073, unit_index=1, unit_type="UNIT_WARRIOR", x=4, y=5)
        cand = Candidate.create(
            ActionKind.MOVE_UNIT, MoveParams(unit=unit, to_x=5, to_y=5), label="Move"
        )
        assert cand.candidate_id == "move:131073:5,5"

    def test_params_must_match_kind(self):
        with pytest.raises(TypeError):
            Candidate.create(
                ActionKind.SET_CIVIC, ResearchParams(tech_type="X"), label="bad"
            )

    def test_reordered_candidates_keep_identity(self):
        a = _three((POTTERY, MINING, ARCHERY))
        b = _three((ARCHERY, POTTERY, MINING))
        assert a.label_to_id == b.label_to_id
        assert [c.candidate_id for c in a.candidates] == [
            c.candidate_id for c in b.candidates
        ]

    def test_reordered_candidates_select_same_action(self):
        answer = _answer(
            [("Pottery", 0.2), ("Mining", 0.7), ("Archery", 0.1)], "Mining"
        )
        a = resolve_choice(_three((POTTERY, MINING, ARCHERY)), answer)
        b = resolve_choice(_three((ARCHERY, MINING, POTTERY)), answer)
        assert a.candidate_id == b.candidate_id == "research:TECHNOLOGY_MINING"

    def test_duplicate_candidates_are_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            _point([_research(*POTTERY), _research(*POTTERY)])

    def test_empty_candidate_set_is_rejected(self):
        with pytest.raises(ValueError):
            _point([])

    def test_colliding_labels_are_disambiguated_independent_of_order(self):
        x = _research("TECHNOLOGY_A", "Same")
        y = _research("TECHNOLOGY_B", "Same")
        p1 = _point([x, y])
        p2 = _point([y, x])
        assert p1.label_to_id == p2.label_to_id
        assert len(set(p1.label_to_id)) == 2


class TestResolveChoice:
    def test_highest_probability_selects_its_candidate(self):
        d = resolve_choice(
            _three(),
            _answer([("Pottery", 0.62), ("Mining", 0.3), ("Archery", 0.08)], "Pottery"),
        )
        assert d.candidate_id == "research:TECHNOLOGY_POTTERY"
        assert d.scores["research:TECHNOLOGY_POTTERY"] == pytest.approx(0.62)
        assert d.selector == "drex"

    def test_tie_prefers_reported_choice_when_it_is_a_maximum(self):
        d = resolve_choice(
            _three(),
            _answer([("Pottery", 0.4), ("Mining", 0.4), ("Archery", 0.2)], "Pottery"),
        )
        assert d.candidate_id == "research:TECHNOLOGY_POTTERY"

    def test_tie_without_reported_choice_uses_lowest_candidate_id(self):
        answer = _answer([("Pottery", 0.4), ("Mining", 0.4), ("Archery", 0.2)])
        a = resolve_choice(_three((POTTERY, MINING, ARCHERY)), answer)
        b = resolve_choice(_three((MINING, ARCHERY, POTTERY)), answer)
        assert a.candidate_id == b.candidate_id == "research:TECHNOLOGY_MINING"

    def test_unknown_option_cannot_execute(self):
        with pytest.raises(MalformedChoice, match="unknown"):
            resolve_choice(
                _three(),
                _answer(
                    [
                        ("Pottery", 0.1),
                        ("Mining", 0.1),
                        ("Archery", 0.1),
                        ("Bronze Working", 0.7),
                    ],
                    "Bronze Working",
                ),
            )

    def test_missing_option_is_rejected(self):
        with pytest.raises(MalformedChoice, match="missing"):
            resolve_choice(
                _three(), _answer([("Pottery", 0.5), ("Mining", 0.5)], "Pottery")
            )

    def test_duplicate_option_is_rejected(self):
        with pytest.raises(MalformedChoice, match="duplicate"):
            resolve_choice(
                _three(),
                _answer(
                    [
                        ("Pottery", 0.5),
                        ("Pottery", 0.2),
                        ("Mining", 0.2),
                        ("Archery", 0.1),
                    ],
                    "Pottery",
                ),
            )

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -0.1, 1.5, "0.5", None, True])
    def test_non_probability_values_are_rejected(self, bad):
        with pytest.raises(MalformedChoice):
            resolve_choice(
                _three(),
                _answer(
                    [("Pottery", bad), ("Mining", 0.5), ("Archery", 0.5)], "Mining"
                ),
            )

    def test_distribution_must_sum_to_one(self):
        with pytest.raises(MalformedChoice, match="sum"):
            resolve_choice(
                _three(),
                _answer(
                    [("Pottery", 0.9), ("Mining", 0.9), ("Archery", 0.9)], "Pottery"
                ),
            )

    def test_reported_choice_must_be_a_maximum(self):
        with pytest.raises(MalformedChoice, match="maximum"):
            resolve_choice(
                _three(),
                _answer(
                    [("Pottery", 0.1), ("Mining", 0.8), ("Archery", 0.1)], "Pottery"
                ),
            )

    def test_reported_choice_must_be_a_known_option(self):
        with pytest.raises(MalformedChoice):
            resolve_choice(
                _three(),
                _answer(
                    [("Pottery", 0.1), ("Mining", 0.8), ("Archery", 0.1)], "Writing"
                ),
            )


class TestSelectionAndStaleness:
    def test_selection_of_stored_candidate_is_accepted(self):
        point = _three()
        d = validate_selection(
            point, "research:TECHNOLOGY_ARCHERY", selector="random-baseline"
        )
        assert d.candidate_id == "research:TECHNOLOGY_ARCHERY"
        assert d.selector == "random-baseline"

    def test_selection_of_unknown_candidate_is_rejected(self):
        with pytest.raises(UnknownCandidate):
            validate_selection(
                _three(), "research:TECHNOLOGY_WRITING", selector="replay"
            )

    def test_forced_decision_requires_exactly_one_candidate(self):
        assert (
            forced_decision(_point([_research(*POTTERY)])).rule
            == "forced_single_candidate"
        )
        with pytest.raises(ValueError):
            forced_decision(_three())

    def test_current_observation_passes(self):
        ensure_current(_three(), "rome:42:T5:1")

    def test_stale_observation_is_rejected(self):
        with pytest.raises(StaleDecision):
            ensure_current(_three(), "rome:42:T5:2")

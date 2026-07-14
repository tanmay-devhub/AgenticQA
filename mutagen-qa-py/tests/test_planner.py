from mutagen.agent.classifier import ClassifiedSurvivor
from mutagen.agent.planner import _function_from_id, plan_specs
from mutagen.mutation.report import Mutant


def _cs(mutant_id, verdict="real_gap", *, file="target.py", kind="comparison"):
    m = Mutant(id=mutant_id, file=file, line=1, status="survived", kind=kind)
    return ClassifiedSurvivor(mutant=m, verdict=verdict, reason="")


def test_function_from_id_standard():
    assert _function_from_id("target.parse_range__mutmut_3") == "parse_range"


def test_function_from_id_bare_tail():
    assert _function_from_id("parse_range__mutmut_42") == "parse_range"


def test_function_from_id_no_match():
    assert _function_from_id("weird_id_no_mutmut_suffix") is None


def test_plan_drops_non_real_gap():
    classified = [
        _cs("target.f__mutmut_1", "equivalent"),
        _cs("target.f__mutmut_2", "message_noise"),
    ]
    assert plan_specs(classified) == []


def test_plan_clusters_by_function():
    classified = [
        _cs("target.f__mutmut_1", kind="comparison"),
        _cs("target.f__mutmut_2", kind="comparison"),
        _cs("target.g__mutmut_1", kind="arithmetic"),
    ]
    specs = plan_specs(classified)
    assert len(specs) == 2
    by_fn = {s.function: s for s in specs}
    assert set(by_fn) == {"f", "g"}
    assert len(by_fn["f"].survivors) == 2
    assert by_fn["f"].dominant_kind == "comparison"
    assert by_fn["g"].dominant_kind == "arithmetic"


def test_plan_picks_dominant_kind_by_majority():
    classified = [
        _cs("target.f__mutmut_1", kind="comparison"),
        _cs("target.f__mutmut_2", kind="constant"),
        _cs("target.f__mutmut_3", kind="constant"),
    ]
    specs = plan_specs(classified)
    assert len(specs) == 1
    assert specs[0].dominant_kind == "constant"


def test_plan_technique_hint_is_populated():
    specs = plan_specs([_cs("target.f__mutmut_1", kind="comparison")])
    assert specs[0].technique_hint
    assert "boundary" in specs[0].technique_hint.lower()

import json
from pathlib import Path

from mutagen.agent.classifier import ClassifiedSurvivor
from mutagen.agent.planner import _lines_in_cluster_span, plan_specs
from mutagen.mutation.coverage import load_coverage
from mutagen.mutation.report import Mutant


def _write_coverage(dir: Path, files: dict[str, dict]) -> None:
    (dir / "coverage.json").write_text(json.dumps({"files": files}), encoding="utf-8")


def test_load_coverage_missing_file_returns_empty(tmp_path):
    assert load_coverage(tmp_path) == {}


def test_load_coverage_parses_missing_lines(tmp_path):
    _write_coverage(
        tmp_path,
        {"target.py": {"executed_lines": [1, 2, 3], "missing_lines": [4, 5]}},
    )
    result = load_coverage(tmp_path)
    assert "target.py" in result
    assert result["target.py"].missing_lines == [4, 5]
    assert result["target.py"].executed_lines == [1, 2, 3]


def test_load_coverage_invalid_json_returns_empty(tmp_path):
    (tmp_path / "coverage.json").write_text("garbage", encoding="utf-8")
    assert load_coverage(tmp_path) == {}


def test_load_coverage_handles_real_pytest_cov_shape(tmp_path):
    # coverage.py's per-file `summary.missing_lines` is a COUNT (int), not a
    # list. Only the top-level `missing_lines` field is iterable. Regression:
    # the loader used to treat the summary field as a list and crash on live
    # runs.
    _write_coverage(
        tmp_path,
        {
            "target.py": {
                "executed_lines": [1, 2],
                "missing_lines": [3, 4],
                "summary": {
                    "covered_lines": 2,
                    "num_statements": 4,
                    "missing_lines": 2,
                    "percent_covered": 50.0,
                },
            }
        },
    )
    result = load_coverage(tmp_path)
    assert result["target.py"].missing_lines == [3, 4]


def test_lines_in_cluster_span_filters_far_lines():
    mutants = [Mutant(id="a", file="target.py", line=10, status="survived")]
    missing = [1, 8, 11, 20, 100]
    # Buffer is [8, 18]; 8 and 11 fall in range.
    assert _lines_in_cluster_span(mutants, missing) == [8, 11]


def test_plan_specs_attaches_uncovered_lines():
    diff = "-x\n+y\n"
    m = Mutant(id="target.f__mutmut_1", file="target.py", line=5, status="survived", diff=diff, kind="comparison")
    classified = [ClassifiedSurvivor(m, "real_gap", "boundary")]
    specs = plan_specs(classified, missing_lines=[4, 6, 30])
    assert len(specs) == 1
    assert specs[0].uncovered_lines == [4, 6]


def test_plan_specs_no_missing_lines_returns_empty_uncovered():
    m = Mutant(id="target.f__mutmut_1", file="target.py", line=5, status="survived", diff="", kind="other")
    specs = plan_specs([ClassifiedSurvivor(m, "real_gap", "x")])
    assert specs[0].uncovered_lines == []

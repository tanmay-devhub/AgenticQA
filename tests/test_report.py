from mutagen.mutation.report import MutationReport, Mutant


def test_kill_rate_zero_denom():
    r = MutationReport(total=0, killed=0, survived=0)
    assert r.kill_rate == 0.0


def test_kill_rate_all_killed():
    r = MutationReport(total=5, killed=5, survived=0)
    assert r.kill_rate == 1.0


def test_kill_rate_excludes_skipped_from_denom():
    r = MutationReport(total=10, killed=8, survived=0, skipped=2)
    assert r.kill_rate == 1.0


def test_kill_rate_mixed():
    r = MutationReport(total=10, killed=6, survived=2, timeout=1, suspicious=1)
    assert r.kill_rate == 0.6


def test_format_summary_reports_filtered_types():
    r = MutationReport(total=1, killed=1, survived=0, disabled_types=["string", "fstring"])
    s = r.format_summary()
    assert "filtered mutation types: string,fstring" in s


def test_format_summary_omits_filter_line_when_none():
    r = MutationReport(total=1, killed=1, survived=0)
    s = r.format_summary()
    assert "filtered" not in s


def test_mutant_defaults():
    m = Mutant(id="x__mutmut_1", file=None, line=None, status="survived")
    assert m.kind == "other"
    assert m.diff is None

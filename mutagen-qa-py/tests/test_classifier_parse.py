from mutagen.agent.classifier import _parse_verdict, classify_survivors
from mutagen.agent.testing import FakeLLM
from mutagen.mutation.report import Mutant


def test_parse_verdict_real_gap():
    v, r = _parse_verdict('{"verdict":"real_gap","reason":"x < 0 diverges"}')
    assert v == "real_gap"
    assert r == "x < 0 diverges"


def test_parse_verdict_equivalent():
    v, _ = _parse_verdict('{"verdict":"equivalent","reason":"same for all inputs"}')
    assert v == "equivalent"


def test_parse_verdict_message_noise():
    v, _ = _parse_verdict('{"verdict":"message_noise","reason":"only message text"}')
    assert v == "message_noise"


def test_parse_verdict_tolerates_surrounding_text():
    text = 'Sure! Here is my verdict:\n{"verdict":"real_gap","reason":"gap"}\nHope that helps.'
    v, r = _parse_verdict(text)
    assert v == "real_gap"
    assert r == "gap"


def test_parse_verdict_unknown_defaults_safe():
    v, r = _parse_verdict('{"verdict":"gibberish","reason":"?"}')
    # Safe fallback: treat as real_gap so we don't silently skip a real survivor.
    assert v == "real_gap"
    assert "gibberish" in r


def test_parse_verdict_no_json_defaults_safe():
    v, _ = _parse_verdict("no braces here")
    assert v == "real_gap"


def test_parse_verdict_malformed_json_defaults_safe():
    v, _ = _parse_verdict("{not really json}")
    assert v == "real_gap"


def test_parse_verdict_reason_truncated():
    long = "x" * 500
    _, r = _parse_verdict('{"verdict":"equivalent","reason":"' + long + '"}')
    assert len(r) <= 200


class _ExplodingLLM(FakeLLM):
    def complete(self, role, *, system, user):
        raise RuntimeError("simulated rate limit")


def test_classify_survivors_defaults_to_real_gap_on_llm_error(tmp_path):
    (tmp_path / "target.py").write_text("def f(): return 1\n", encoding="utf-8")
    survivor = Mutant(
        id="target.f__mutmut_1",
        file="target.py",
        line=1,
        status="survived",
        diff="- return 1\n+ return 2\n",
        kind="constant",
    )
    llm = _ExplodingLLM()
    result = classify_survivors(
        llm,
        target_source=tmp_path / "target.py",
        survivors=[survivor],
        cache_dir=tmp_path / ".mutagen",
    )
    assert len(result) == 1
    assert result[0].verdict == "real_gap"
    assert "planner call failed" in result[0].reason
    # No cache should be written for a failed call so a retry can succeed.
    assert not (tmp_path / ".mutagen" / "classifier_cache.json").exists()

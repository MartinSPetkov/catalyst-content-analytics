"""
Tests for shared/antislop.py

Verifies that the pattern lists catch the right things and that clean text
passes through without triggering a (slow, LLM-dependent) rewrite.
"""
import pytest

from shared.antislop import check, clean


class TestCheck:
    """check() returns a list of violation strings, empty if clean."""

    def test_clean_text_has_no_violations(self):
        text = "Long-form posts on GTM strategy averaged 4.2% engagement last quarter."
        assert check(text) == []

    def test_detects_hollow_intensifier_revolutionary(self):
        violations = check("This revolutionary approach changes everything.")
        assert any("revolutionary" in v for v in violations)

    def test_detects_hollow_intensifier_game_changing(self):
        violations = check("A game-changing framework for B2B content.")
        assert any("game-changing" in v for v in violations)

    def test_detects_filler_opener_in_todays_world(self):
        violations = check("In today's world, attention is scarce.")
        assert any("filler opener" in v for v in violations)

    def test_detects_filler_opener_its_worth_noting(self):
        violations = check("It's worth noting that engagement rates vary by format.")
        assert any("filler opener" in v for v in violations)

    def test_detects_summarising_closer_in_summary(self):
        violations = check("In summary, list posts outperform long-form on LinkedIn.")
        assert any("summarising closer" in v for v in violations)

    def test_detects_summarising_closer_to_wrap_up(self):
        violations = check("To wrap up: publish more stat-driven hooks.")
        assert any("summarising closer" in v for v in violations)

    def test_case_insensitive(self):
        violations = check("REVOLUTIONARY content strategy ahead.")
        assert any("revolutionary" in v for v in violations)

    def test_detects_multiple_violations(self):
        text = "In today's world, this revolutionary and seamless approach is transformative."
        violations = check(text)
        assert len(violations) >= 3

    def test_partial_word_not_matched_by_word_boundary(self):
        """'powerfully' should not trigger the \bpowerful\b pattern."""
        violations = check("The argument was made powerfully.")
        assert not any("powerful" in v for v in violations)

    def test_em_dash_detected(self):
        violations = check("Content ops — the hidden lever.")
        assert any("em dash" in v for v in violations)


class TestClean:
    """clean() returns the original text unchanged when there are no violations."""

    def test_clean_text_returned_unchanged(self):
        text = "Stat-driven hooks on LinkedIn average 5.1% engagement across 12 posts."
        assert clean(text) == text

    def test_clean_does_not_call_llm_on_clean_input(self, monkeypatch):
        """Verify no LLM subprocess is spawned when the text is already clean."""
        called = []

        def fake_call(prompt):
            called.append(prompt)
            return "rewritten"

        monkeypatch.setattr("shared.llm.call", fake_call)
        clean("Stat-driven hooks outperform question hooks by 2.3x on LinkedIn.")
        assert called == [], "LLM should not be called when text has no violations"

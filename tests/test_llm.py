"""
Tests for shared/llm.py

Tests the pure-Python helpers that don't require a live claude subprocess.
"""
import os
import sys

import pytest


# ── Import guard ──────────────────────────────────────────────────────────────

def test_raises_if_api_key_set(monkeypatch):
    """llm.py must raise RuntimeError at import time if ANTHROPIC_API_KEY is set."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    # Force re-import by removing the cached module
    if "shared.llm" in sys.modules:
        del sys.modules["shared.llm"]
    if "shared" in sys.modules:
        del sys.modules["shared"]

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        import shared.llm  # noqa: F401

    # Clean up so other tests get a fresh import
    for key in list(sys.modules.keys()):
        if "shared" in key:
            del sys.modules[key]


# ── _strip_fences ─────────────────────────────────────────────────────────────

def _get_strip_fences():
    """Import _strip_fences without triggering the ANTHROPIC_API_KEY guard."""
    if "ANTHROPIC_API_KEY" in os.environ:
        del os.environ["ANTHROPIC_API_KEY"]
    for key in list(sys.modules.keys()):
        if "shared" in key:
            del sys.modules[key]
    from shared.llm import _strip_fences
    return _strip_fences


class TestStripFences:
    """_strip_fences removes markdown code blocks that claude sometimes wraps JSON in."""

    def setup_method(self):
        self._strip = _get_strip_fences()

    def test_plain_json_unchanged(self):
        raw = '[{"a": 1}]'
        assert self._strip(raw) == raw

    def test_strips_json_fence(self):
        raw = '```json\n[{"a": 1}]\n```'
        assert self._strip(raw) == '[{"a": 1}]'

    def test_strips_plain_fence(self):
        raw = '```\n[{"a": 1}]\n```'
        assert self._strip(raw) == '[{"a": 1}]'

    def test_strips_leading_whitespace(self):
        raw = '  [{"a": 1}]  '
        assert self._strip(raw) == '[{"a": 1}]'

    def test_multiline_json_preserved(self):
        raw = '```json\n[\n  {"a": 1},\n  {"b": 2}\n]\n```'
        result = self._strip(raw)
        assert result.startswith("[")
        assert '"a": 1' in result

    def test_no_closing_fence_leaves_opening(self):
        """Partial fence — only opening stripped, closing absent."""
        raw = '```json\n[{"a": 1}]'
        result = self._strip(raw)
        # Opening fence line is removed; closing would be removed only if present
        assert not result.startswith("```")

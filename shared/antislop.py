import re
from pathlib import Path

_RULES_PATH = Path(__file__).parent.parent / "antislop_rules.md"
_RULES_TEXT = _RULES_PATH.read_text()

_EM_DASH = re.compile(r"—")

_FILLER_OPENERS = [
    r"in today'?s world",
    r"it'?s worth noting",
    r"as we can see",
    r"at the end of the day",
    r"in conclusion",
    r"it goes without saying",
    r"needless to say",
    r"the fact of the matter is",
]

_SUMMARISING_CLOSERS = [
    r"in summary",
    r"to wrap up",
    r"as we'?ve seen",
    r"in closing",
]

_HOLLOW_INTENSIFIERS = [
    r"\brevolutionary\b",
    r"\bgame-changing\b",
    r"\bunprecedented\b",
    r"\btransformative\b",
    r"\bcutting-edge\b",
    r"\binnovative\b",
    r"\bpowerful\b",
    r"\brobust\b",
    r"\bseamless\b",
    r"\bnext-level\b",
]

_ALL_PATTERNS = (
    [("em dash", p) for p in [r"—"]]
    + [("filler opener", p) for p in _FILLER_OPENERS]
    + [("summarising closer", p) for p in _SUMMARISING_CLOSERS]
    + [("hollow intensifier", p) for p in _HOLLOW_INTENSIFIERS]
)


def check(text: str) -> list[str]:
    violations = []
    lower = text.lower()
    for label, pattern in _ALL_PATTERNS:
        if re.search(pattern, lower):
            violations.append(f"{label}: matched /{pattern}/")
    return violations


def clean(text: str) -> str:
    violations = check(text)
    if not violations:
        return text
    from shared import llm
    prompt = (
        f"Rewrite the following text so it complies with all rules in these guidelines:\n\n"
        f"{_RULES_TEXT}\n\n"
        f"Violations found:\n" + "\n".join(f"- {v}" for v in violations) +
        f"\n\nText to rewrite:\n{text}\n\n"
        f"Return only the rewritten text. No commentary."
    )
    return llm.call(prompt)

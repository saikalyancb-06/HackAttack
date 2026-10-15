"""PII masking: raw values never reach the LLM, the ledger, the audit log or the knowledge graph.

mask(text) -> (masked_text, mapping)   mapping = {"<EMAIL_1>": "a@b.com", ...}
Pass an existing mapping to keep placeholders stable across calls (the project vault does this).
"""
from __future__ import annotations

import re
from typing import Any

from privacy.confidence_engine import validate_luhn, validate_verhoeff

# Order matters: longer / more specific patterns first so digits are not claimed twice.
_PATTERNS: list[tuple[str, re.Pattern, Any]] = [
    ("SECRET", re.compile(r"\b(?:gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|xox[bap]-[A-Za-z0-9\-]{10,})\b"), None),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), None),
    ("CARD", re.compile(r"(?<!\d)(?:\d{4}[ \-]?){3}\d{4}(?!\d)"), lambda s: validate_luhn(re.sub(r"\D", "", s))),
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}(?!\d)"), lambda s: validate_verhoeff(re.sub(r"\D", "", s))),
    ("PAN", re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), None),
    ("PHONE", re.compile(r"(?<![\w\d])(?:\+91[ \-]?|0)?[6-9]\d{4}[ \-]?\d{5}(?!\d)|(?<![\w\d])\+\d{1,3}[ \-]?\(?\d{2,4}\)?[ \-]?\d{3,4}[ \-]?\d{3,4}(?!\d)"), None),
    ("UPI", re.compile(r"\b[A-Za-z0-9.\-_]{2,64}@(?:ok)?(?:upi|ybl|paytm|axl|ibl|sbi|icici|hdfcbank|apl|oksbi|okaxis|okicici|okhdfcbank)\b", re.I), None),
    ("PASSWORD", re.compile(r"(?i)\b(?:password|passwd|pwd|passcode|otp|pin)\s*[:=]\s*\S+"), None),
]

PLACEHOLDER_RE = re.compile(r"<(SECRET|EMAIL|CARD|AADHAAR|PAN|PHONE|UPI|PASSWORD)_\d+>")


def _placeholder_for(label: str, raw: str, mapping: dict) -> str:
    for ph, value in mapping.items():
        if value == raw and ph.startswith(f"<{label}_"):
            return ph
    n = 1 + sum(1 for ph in mapping if ph.startswith(f"<{label}_"))
    ph = f"<{label}_{n}>"
    mapping[ph] = raw
    return ph


def mask(text: str, mapping: dict | None = None) -> tuple[str, dict]:
    """Replace PII with stable placeholders. Returns (masked_text, mapping). Mutates `mapping` if given."""
    if mapping is None:
        mapping = {}
    if not isinstance(text, str) or not text:
        return text, mapping
    out = text
    for label, pattern, validator in _PATTERNS:
        def repl(m: re.Match) -> str:
            raw = m.group(0)
            if PLACEHOLDER_RE.fullmatch(raw):
                return raw
            if validator is not None and not validator(raw):
                return raw
            return _placeholder_for(label, raw, mapping)
        out = pattern.sub(repl, out)
    return out, mapping


def mask_obj(obj: Any, mapping: dict | None = None) -> tuple[Any, dict]:
    """mask() applied to every string inside dicts/lists (keys included)."""
    if mapping is None:
        mapping = {}
    if isinstance(obj, str):
        return mask(obj, mapping)[0], mapping
    if isinstance(obj, dict):
        return {mask(str(k), mapping)[0] if isinstance(k, str) else k: mask_obj(v, mapping)[0] for k, v in obj.items()}, mapping
    if isinstance(obj, (list, tuple)):
        return [mask_obj(v, mapping)[0] for v in obj], mapping
    return obj, mapping


def unmask(text_or_obj: Any, mapping: dict) -> Any:
    if not mapping:
        return text_or_obj
    if isinstance(text_or_obj, str):
        if "<" not in text_or_obj:
            return text_or_obj
        return PLACEHOLDER_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text_or_obj)
    if isinstance(text_or_obj, dict):
        return {k: unmask(v, mapping) for k, v in text_or_obj.items()}
    if isinstance(text_or_obj, list):
        return [unmask(v, mapping) for v in text_or_obj]
    return text_or_obj


def contains_pii(text: str) -> bool:
    masked, m = mask(text)
    return bool(m)


if __name__ == "__main__":
    t = "Mail ravi.k@example.com or call +91 98765 43210, PAN ABCDE1234F, key gsk_abcdefghijklmnopqrstuvwxyz123"
    masked, m = mask(t)
    assert "ravi.k@example.com" not in masked and "<EMAIL_1>" in masked, masked
    assert "98765" not in masked and "<PHONE_1>" in masked, masked
    assert "<PAN_1>" in masked and "<SECRET_1>" in masked, masked
    assert unmask(masked, m) == t
    m2: dict = {}
    a, _ = mask("x a@b.co y", m2)
    b, _ = mask("again a@b.co and c@d.io", m2)
    assert a == "x <EMAIL_1> y" and b == "again <EMAIL_1> and <EMAIL_2>", (a, b)
    assert mask("No PII here, version 2026-10-15, 3 internships")[1] == {}
    obj, mm = mask_obj({"to": "z@z.com", "rows": [{"phone": "9876543210"}]})
    assert obj["to"] == "<EMAIL_1>" and obj["rows"][0]["phone"] == "<PHONE_1>"
    assert unmask(obj, mm)["rows"][0]["phone"] == "9876543210"
    print("guard.pii self-check OK")

"""PII mask/unmask (A2). CONTRACTS §7, §11."""

import re
from typing import Any

EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PAN_PATTERN = re.compile(r"(?<![A-Z0-9])[A-Z]{5}[0-9]{4}[A-Z](?![A-Z0-9])")
AADHAAR_PATTERN = re.compile(r"(?<!\d)(?:\d{4}\s\d{4}\s\d{4}|\d{12})(?!\d)")
PHONE_PATTERN = re.compile(r"(?:(?<!\d)\+91[\s\-]?)?[6-9]\d{9}(?!\d)")

PATTERNS = [
    ("EMAIL", EMAIL_PATTERN),
    ("PAN", PAN_PATTERN),
    ("AADHAAR", AADHAAR_PATTERN),
    ("PHONE", PHONE_PATTERN),
]


def mask(text: str) -> tuple[str, dict]:
    """Mask PII in text using regex.

    Returns (masked_text, mapping) where mapping is {placeholder: raw_value}.
    Same raw value gets the same placeholder within the returned mapping.
    """
    if not isinstance(text, str) or not text:
        return text, {}

    mapping: dict[str, str] = {}
    current_text = text

    for kind, pattern in PATTERNS:
        matches = pattern.findall(current_text)
        if not matches:
            continue
        # Preserve discovery order of unique matches
        unique_matches: list[str] = []
        for m in matches:
            if m not in unique_matches:
                unique_matches.append(m)

        count = 1
        val_to_ph: dict[str, str] = {}
        for val in unique_matches:
            ph = f"<{kind}_{count}>"
            count += 1
            val_to_ph[val] = ph
            mapping[ph] = val

        # Replace longer values first to prevent substring collisions
        for val in sorted(unique_matches, key=len, reverse=True):
            ph = val_to_ph[val]
            current_text = current_text.replace(val, ph)

    return current_text, mapping


def unmask(text_or_obj: Any, mapping: dict) -> Any:
    """Unmask placeholders back to raw values. Recursively handles str, dict, list."""
    if not mapping:
        return text_or_obj

    if isinstance(text_or_obj, str):
        res = text_or_obj
        for ph, val in mapping.items():
            if ph in res:
                res = res.replace(ph, str(val))
        return res

    if isinstance(text_or_obj, dict):
        return {unmask(k, mapping): unmask(v, mapping) for k, v in text_or_obj.items()}

    if isinstance(text_or_obj, list):
        return [unmask(item, mapping) for item in text_or_obj]

    if isinstance(text_or_obj, tuple):
        return tuple(unmask(item, mapping) for item in text_or_obj)

    if isinstance(text_or_obj, set):
        return {unmask(item, mapping) for item in text_or_obj}

    return text_or_obj


if __name__ == "__main__":
    import time

    t0 = time.time()
    sample = (
        "Send email to alice@test.com and alice@test.com. "
        "Call +91 9876543210 or 9876543210. "
        "Aadhaar is 1234 5678 9012 or 999988887777. "
        "PAN is ABCDE1234F."
    )
    masked, m = mask(sample)

    assert "<EMAIL_1>" in masked
    assert "alice@test.com" not in masked
    assert masked.count("<EMAIL_1>") == 2, "Same email should get the same placeholder"
    assert "<PAN_1>" in masked
    assert "ABCDE1234F" not in masked
    assert "<AADHAAR_1>" in masked
    assert "<AADHAAR_2>" in masked
    assert "<PHONE_1>" in masked

    # Round trip
    recovered = unmask(masked, m)
    assert recovered == sample, f"Unmask mismatch:\nOriginal: {sample}\nRecovered: {recovered}"

    # Object unmask
    obj = {"user": "<EMAIL_1>", "ids": ["<PAN_1>", 123]}
    unmasked_obj = unmask(obj, m)
    assert unmasked_obj == {"user": "alice@test.com", "ids": ["ABCDE1234F", 123]}

    print(f"guard.pii self-check PASSED in {time.time() - t0:.3f}s")

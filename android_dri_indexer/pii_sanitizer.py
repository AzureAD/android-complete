"""Lightweight PII sanitisation for ICM ticket text.

Self-contained — no dependency on DRICopilot src/ tree.
"""

import re
from typing import Dict, Tuple

# Pattern definitions: (regex, replacement)
_PII_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
        "[EMAIL_REDACTED]",
    ),
    (
        "ssn",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[SSN_REDACTED]",
    ),
    (
        "phone_us",
        re.compile(r"\b(?:\+1[-.\s]?)?(?:\([0-9]{3}\)|[0-9]{3})[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b"),
        "[PHONE_REDACTED]",
    ),
    (
        "credit_card",
        re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|3[0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b"),
        "[CREDIT_CARD_REDACTED]",
    ),
    (
        "medical_record",
        re.compile(r"\b(?:MRN|MR|Patient ID|Record Number)[\s:]*\d+\b", re.IGNORECASE),
        "[MEDICAL_RECORD_REDACTED]",
    ),
    (
        "dob",
        re.compile(r"\b(?:DOB|Date of Birth|Born)[\s:]*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", re.IGNORECASE),
        "[DOB_REDACTED]",
    ),
    (
        "address",
        re.compile(r"\b\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Place|Pl)\b", re.IGNORECASE),
        "[ADDRESS_REDACTED]",
    ),
    (
        "name_patterns",
        re.compile(r"\b(?:Patient|Customer|User)\s+(?:Name|ID)[\s:]*[A-Za-z\s]+\b", re.IGNORECASE),
        "[NAME_REDACTED]",
    ),
]

_FALSE_POSITIVE_EMAIL_DOMAINS = frozenset([
    "example.com", "example.org", "test.com", "localhost",
    "contoso.com", "fabrikam.com", "northwind.com",
    "microsoft.com", "outlook.com",
])

_FALSE_POSITIVE_SSNS = frozenset(["123456789", "000000000", "111111111"])


def _is_false_positive(pattern_type: str, matched_text: str) -> bool:
    if pattern_type == "email":
        for domain in _FALSE_POSITIVE_EMAIL_DOMAINS:
            if matched_text.lower().endswith(domain):
                return True
    if pattern_type == "ssn" and matched_text.replace("-", "") in _FALSE_POSITIVE_SSNS:
        return True
    return False


def sanitize_text(text: str) -> Tuple[str, Dict[str, int]]:
    """Redact PII from *text*, returning (sanitized_text, stats_dict)."""
    if not text:
        return text, {}

    result = text
    stats: Dict[str, int] = {}

    for name, pattern, replacement in _PII_PATTERNS:
        matches = list(pattern.finditer(result))
        valid = [m for m in matches if not _is_false_positive(name, m.group())]
        if valid:
            stats[name] = len(valid)
            for m in reversed(valid):
                result = result[: m.start()] + replacement + result[m.end() :]

    return result, stats

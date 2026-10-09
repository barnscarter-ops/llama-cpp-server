"""Redact a task summary before it leaves the machine for Jev.

Chief already sends a composed summary; this is the second line. It errs toward removing too much:
a routing hint does not need a key, a phone number or an address, and a missed secret cannot be recalled.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

# Order matters: whole blocks and labelled values go before the generic shapes inside them.
_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, flags)
    for p, flags in (
        (r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S),
        (r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I),
        (r"\bBasic\s+[A-Za-z0-9+/=]{12,}", re.I),
        # Same labels the repo bridge already treats as sensitive, redacted to the end of the line.
        (r"(?:ssh|api[_ -]?key|private[_ -]?key|access[_ -]?key|secret|token|credential|password|passwd|id_ed25519|id_rsa)[^\r\n]*", re.I),
        (r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}", 0),
        (r"\bnvapi-[A-Za-z0-9_-]{16,}", 0),
        (r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}", 0),
        (r"\bgithub_pat_[A-Za-z0-9_]{20,}", 0),
        (r"\bxox[abprs]-[A-Za-z0-9-]{10,}", 0),
        (r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", 0),
        (r"\bAIza[A-Za-z0-9_-]{30,}", 0),
        (r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", 0),
        (r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.I),  # credentials inside a URL
        # Phone and email shapes from the brief redactor; addresses from the repo bridge.
        (r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", 0),
        (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", 0),
        (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", 0),
        # Anything long and key-shaped that the specific rules above did not name.
        (r"\b[A-Fa-f0-9]{32,}\b", 0),
        (r"\b[A-Za-z0-9+/_-]{40,}={0,2}(?![A-Za-z0-9+/_-])", 0),
    )
)


def redact_summary(text: str) -> tuple[str, int]:
    """Returns (clean text, number of spans removed)."""
    total = 0
    for pattern in _PATTERNS:
        text, n = pattern.subn(REDACTED, text)
        total += n
    return text, total

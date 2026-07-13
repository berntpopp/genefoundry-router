"""One bounded secret/token predicate shared by image and archive inspection."""

from __future__ import annotations

import base64
import contextlib
import re

_MAX_SCAN_BYTES = 64 * 1024
_SECRET_WORDS = re.compile(
    r"(?i)(?:^|[^a-z0-9])(api[_ -]?key|token|secret|password|passwd|credential|authorization|private[_ -]?key|bearer)(?:$|[^a-z0-9])"
)
_TOKEN_FORMATS = re.compile(
    r"(?i)(gh[pousr]_[a-z0-9]{20,}|github_pat_[a-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}|AIza[a-z0-9_-]{30,}|xox[baprs]-[a-z0-9-]{20,}|eyJ[a-z0-9_-]{8,}\.[a-z0-9_-]{8,}\.[a-z0-9_-]{8,})"
)


def secret_shaped(value: object, *, semantic_words: bool = True) -> bool:
    """Detect semantic secret words and common token forms without leaking values."""
    if not isinstance(value, str):
        return False
    if len(value.encode("utf-8", "surrogatepass")) > _MAX_SCAN_BYTES:
        return True
    compact = "".join(value.split())
    candidates = [value.strip(), compact]
    if len(compact) >= 12:
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            candidates.append(base64.b64decode(compact, validate=True).decode("utf-8", "strict"))
    return any(
        (semantic_words and _SECRET_WORDS.search(candidate)) or _TOKEN_FORMATS.search(candidate)
        for candidate in candidates
    )

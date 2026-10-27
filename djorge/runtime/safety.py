"""Input limits and regex safety. The DSL never executes code; these guards
bound resource use (catastrophic regex backtracking, oversized inputs)."""
from __future__ import annotations

import re

from ..errors import DjorgeError

_NESTED_QUANT = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")
_BACKREF = re.compile(r"\\[1-9]|\(\?P=")


def check_regex(pattern: str, max_chars: int) -> re.Pattern:
    if len(pattern) > max_chars:
        raise DjorgeError("UNSAFE_PATTERN", f"regex longer than {max_chars} characters")
    if _NESTED_QUANT.search(pattern):
        raise DjorgeError("UNSAFE_PATTERN", "nested quantifiers (e.g. (a+)+) are rejected to prevent catastrophic backtracking")
    if _BACKREF.search(pattern):
        raise DjorgeError("UNSAFE_PATTERN", "backreferences are not supported")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise DjorgeError("TYPE_ERROR", f"invalid regex: {e}")


def check_size(name: str, text: str, limit: int):
    if len(text) > limit:
        raise DjorgeError("RESOURCE_LIMIT", f"{name} exceeds {limit} characters", {"length": len(text)})

"""Structured error model. Every failure surfaces as a DjorgeError with a stable code."""
from __future__ import annotations

from typing import Any, Dict, Optional

CODES = {
    "PARSE_ERROR", "TYPE_ERROR", "UNSUPPORTED_OPERATION", "UNSUPPORTED_MODE", "MODEL_ERROR",
    "MODEL_UNAVAILABLE", "TIMEOUT", "RESOURCE_LIMIT", "LOW_CONFIDENCE", "ABSTENTION",
    "INVALID_CONTEXT", "UNBOUND_SOURCE", "UNSAFE_PATTERN", "INVALID_REQUEST", "ADAPTATION_REJECTED",
    "SAFETY_BLOCKED", "BUSY", "RATE_LIMITED", "UNAUTHORIZED", "FORBIDDEN", "UNSUPPORTED_MEDIA_TYPE",
    "LENGTH_REQUIRED", "METHOD_NOT_ALLOWED",
}


class DjorgeError(Exception):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        assert code in CODES, code
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.details = code, message, details or {}

    def to_dict(self) -> dict:
        d = {"error": self.code, "message": self.message}
        if self.details:
            d["details"] = self.details
        return d

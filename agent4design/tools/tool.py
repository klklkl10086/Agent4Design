"""Small dependency-free utility helpers."""

import re


def sanitize_identifier(raw: str, fallback: str = "unnamed") -> str:
    """Convert arbitrary text to a stable identifier used by XMI and Rhapsody."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", raw or "")
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned


# Kept as a short alias for existing callers.
sanitize = sanitize_identifier

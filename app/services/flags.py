"""Per-instance flag material.

A challenge stores its flag as plaintext ``flag_template`` plus an HMAC digest.
When a solution starts a container, the platform substitutes the template with a fresh
random value and injects it as the ``FLAG`` environment variable, so every instance
carries its own secret and command-line archives that write ``$FLAG`` into their flag
file work without modification.
"""

from __future__ import annotations

import re
import secrets

PLACEHOLDER = "<RANDOM>"  # replaced with a random hex secret per instance
DEFAULT_PREFIX = "SYC"
FRAMED_TEMPLATE = re.compile(r"^(?P<prefix>[A-Za-z0-9_]{1,16})\{(?P<body>.*)\}$", re.DOTALL)
SUBMITTED_FLAG = re.compile(r"^[^\s{}]{1,480}\{[^\s{}]{1,400}\}$")
RANDOM_HEX_LENGTH = 32


def normalize_template(flag: str, default_prefix: str = DEFAULT_PREFIX) -> str:
    """Turn an admin-supplied flag into an injectable template.

    The literal flag is kept as-is, including a ``<RANDOM>`` placeholder, so
    ``SYC{<RANDOM>}`` becomes a fresh secret per instance. A flag that does not look
    like ``PREFIX{...}`` at all is rebuilt as ``<PREFIX>{<RANDOM>}`` so a shared
    literal secret is never reused per instance.
    """
    candidate = flag.strip()
    if FRAMED_TEMPLATE.match(candidate):
        return candidate
    prefix = (default_prefix or DEFAULT_PREFIX).strip() or DEFAULT_PREFIX
    return f"{prefix}{{{PLACEHOLDER}}}"


def render_instance_flag(template: str | None, default_prefix: str = DEFAULT_PREFIX) -> str:
    """Produce the concrete flag that a new instance should carry."""
    candidate = (template or "").strip()
    if not candidate:
        prefix = (default_prefix or DEFAULT_PREFIX).strip() or DEFAULT_PREFIX
        return f"{prefix}{{{secrets.token_hex(RANDOM_HEX_LENGTH // 2)}}}"
    if PLACEHOLDER not in candidate:
        return candidate
    return candidate.replace(PLACEHOLDER, secrets.token_hex(RANDOM_HEX_LENGTH // 2))


def looks_like_flag(value: str) -> bool:
    """Cheap sanity check used before storing a player-submitted instance flag."""
    return bool(SUBMITTED_FLAG.match(value.strip()))

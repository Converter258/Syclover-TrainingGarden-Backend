"""Flag material for challenges.

A challenge stores its flag as plaintext ``flag_template`` plus an HMAC digest of the
literal flag. When the administrator enables the dynamic flag switch, the ``RAND``
token inside the template is replaced with a fresh random string for every instance and
injected into the container as the ``FLAG`` environment variable, so command-line
archives that write ``$FLAG`` into their flag file keep working unchanged.

Rules that keep administrator intent intact:

* the switch is the only thing that makes a flag dynamic - a literal flag that merely
  contains the letters ``RAND`` is left alone;
* the legacy ``<RANDOM>`` placeholder is converted to ``RAND`` instead of being dropped,
  so an old flag never turns into an unusable template;
* a flag always keeps a ``PREFIX{...}`` shape, because that is what the submission
  formatcheck expects.
"""

from __future__ import annotations

import re
import secrets

RAND_TOKEN = "RAND"
LEGACY_PLACEHOLDER = "<RANDOM>"  # written by Alpha0.0.2, still accepted
DEFAULT_PREFIX = "SYC"
FRAMED_TEMPLATE = re.compile(r"^(?P<prefix>[A-Za-z0-9_]{1,16})\{(?P<body>.*)\}$", re.DOTALL)
SUBMITTED_FLAG = re.compile(r"^[^\s{}]{1,480}\{[^\s{}]{1,400}\}$")
RANDOM_HEX_LENGTH = 32


def random_secret() -> str:
    return secrets.token_hex(RANDOM_HEX_LENGTH // 2)


def template_wants_random(template: str | None) -> bool:
    """True when the template still carries a token that must be replaced per instance."""
    candidate = template or ""
    return RAND_TOKEN in candidate or LEGACY_PLACEHOLDER in candidate


def dynamic_flag_for_template(template: str | None, requested: bool) -> bool:
    """The stored switch: an explicit administrator choice wins, otherwise infer the token.

    A literal flag such as ``SYC{y0u_4r3_m4tH_G3nius_RAND}`` must not flip the switch on
    its own, so only the unambiguous ``<RANDOM>`` placeholder is treated as a request.
    """
    return bool(requested) or LEGACY_PLACEHOLDER in (template or "")


def randomize_template(template: str, replacement: str | None = None) -> str:
    """Replace the dynamic token with a concrete value; RAND wins over the legacy form."""
    secret = replacement or random_secret()
    if LEGACY_PLACEHOLDER in template:
        template = template.replace(LEGACY_PLACEHOLDER, RAND_TOKEN)
    if RAND_TOKEN in template:
        return template.replace(RAND_TOKEN, secret)
    return template


def normalize_template(flag: str, default_prefix: str = DEFAULT_PREFIX) -> str:
    """Normalize an administrator supplied flag without losing its text.

    The stored template always looks like ``PREFIX{...}``. A bare ``<RANDOM>`` placeholder
    is rewritten to ``RAND`` so an older flag keeps working, and a flag that has no
    ``PREFIX{...}`` shape at all is wrapped in the configured prefix.
    """
    candidate = flag.strip()
    if LEGACY_PLACEHOLDER in candidate and not FRAMED_TEMPLATE.match(candidate):
        # e.g. "<RANDOM>" or "prefix-<RANDOM>": wrap it so the rendered value stays a flag.
        prefix = (default_prefix or DEFAULT_PREFIX).strip() or DEFAULT_PREFIX
        body = re.sub(r"[^A-Za-z0-9_.\-{}]", "_", candidate.replace(LEGACY_PLACEHOLDER, RAND_TOKEN))
        return f"{prefix}{{{body or RAND_TOKEN}}}"
    if FRAMED_TEMPLATE.match(candidate):
        return candidate.replace(LEGACY_PLACEHOLDER, RAND_TOKEN)
    prefix = (default_prefix or DEFAULT_PREFIX).strip() or DEFAULT_PREFIX
    return f"{prefix}{{{RAND_TOKEN}}}"


def render_instance_flag(
    template: str | None,
    dynamic: bool = False,
    default_prefix: str = DEFAULT_PREFIX,
) -> str:
    """Produce the concrete flag a new instance should carry."""
    candidate = (template or "").strip()
    if not candidate:
        prefix = (default_prefix or DEFAULT_PREFIX).strip() or DEFAULT_PREFIX
        return f"{prefix}{{{random_secret()}}}" if dynamic else f"{prefix}{{training}}"
    if not dynamic:
        return candidate
    if template_wants_random(candidate):
        return randomize_template(candidate)
    match = FRAMED_TEMPLATE.match(candidate)
    if match:
        # The switch is on but the template has no token: append a secret inside the braces.
        return f"{match.group('prefix')}{{{match.group('body')}{random_secret()}}}"
    return f"{candidate}{{{random_secret()}}}"


def looks_like_flag(value: str) -> bool:
    """Cheap sanity check used before storing a player-submitted instance flag."""
    return bool(SUBMITTED_FLAG.match(value.strip()))

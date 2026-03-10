"""Shared utility helpers for theraFlow.

Centralises small, reusable functions that are needed across multiple
sub-packages.
"""

from __future__ import annotations


def mask_phone(phone: str | None) -> str | None:
    """Return a masked version of *phone* safe for log output.

    Keeps the first 5 and last 2 characters visible; replaces every digit
    in between with the block character ``■`` so raw phone numbers never
    appear in structured log fields.

    Examples::

        mask_phone("5511999999999")  # "55119■■■■■■99"
        mask_phone("551199")         # "551199"   (≤7 chars — returned as-is)
        mask_phone(None)             # None

    Args:
        phone: Phone number string (any format), or ``None``.

    Returns:
        Masked string, or ``None`` when *phone* is ``None`` / empty.
    """
    if not phone:
        return phone
    n = len(phone)
    if n <= 7:
        return phone
    return phone[:5] + "\u25a0" * (n - 7) + phone[-2:]

"""Money handling for OlbosTax.

Every monetary amount in the system is a :class:`decimal.Decimal`.  Binary
floating point is never used for tax amounts: ``0.1 + 0.2 != 0.3`` is an
acceptable rounding artifact in a graphics pipeline and an unacceptable one on
a tax return that a taxpayer signs under penalty of perjury.

Two rounding policies exist and they are deliberately separate:

``to_cents``
    Used for money the taxpayer typed in or that was read off a document.
    Preserves cents, so a W-2 Box 1 of ``58240.12`` stays ``58240.12``.

``to_whole_dollars``
    Used when a value is placed on a return line.  Both the IRS and the
    Oklahoma Tax Commission permit whole-dollar reporting, and the engine
    applies it at documented points only -- never silently mid-calculation --
    so that a printed return foots against its own line items.

Rounding is ``ROUND_HALF_UP``, which matches the "50 cents or more rounds up"
instruction in the Form 1040 instructions, and *not* Python's default
``ROUND_HALF_EVEN``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

__all__ = ["Money", "ZERO", "to_cents", "to_whole_dollars", "money", "clamp_non_negative"]

Money = Decimal

ZERO: Money = Decimal("0")

_CENTS = Decimal("0.01")
_DOLLARS = Decimal("1")


def money(value: object) -> Money:
    """Coerce ``value`` into a :class:`Decimal` without ever going via ``float``.

    ``float`` inputs are rejected rather than converted.  A float that reached
    this function is a bug upstream, and silently accepting it would bake an
    unrepresentable value into a tax return.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # bool is an int subclass; almost certainly a bug.
        raise TypeError("bool is not a monetary amount")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value.replace(",", "").replace("$", "").strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a monetary amount: {value!r}") from exc
    if isinstance(value, float):
        raise TypeError(
            "float is not accepted as a monetary amount; pass a str, int or Decimal"
        )
    raise TypeError(f"cannot interpret {type(value).__name__} as a monetary amount")


def to_cents(value: object) -> Money:
    """Round to two decimal places, half away from zero."""
    return money(value).quantize(_CENTS, rounding=ROUND_HALF_UP)


def to_whole_dollars(value: object) -> Money:
    """Round to whole dollars, half away from zero (Form 1040 convention)."""
    return money(value).quantize(_DOLLARS, rounding=ROUND_HALF_UP)


def clamp_non_negative(value: object) -> Money:
    """Return ``value`` or zero, whichever is larger.

    Tax forms are full of "if zero or less, enter -0-" instructions.  Naming
    that operation makes the calculation code read like the form it implements.
    """
    amount = money(value)
    return amount if amount > 0 else ZERO

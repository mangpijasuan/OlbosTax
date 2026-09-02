"""Reusable calculation primitives shared by every jurisdiction module."""

from .primitives import (
    apply_bracket_schedule,
    marginal_rate,
    phase_out_by_increment,
    phase_out_ratably,
    rate_for_amount,
)

__all__ = [
    "apply_bracket_schedule",
    "marginal_rate",
    "phase_out_by_increment",
    "phase_out_ratably",
    "rate_for_amount",
]

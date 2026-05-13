"""Compatibility wrapper for the restored core intent classifier."""

from core.intent_classifier import (
    I0_CONVERSATION,
    I1_FACT,
    I2_PROBLEM,
    I3_CREATIVE,
    I4_SUPPORT,
    I5_STRATEGY,
    classify,
)

__all__ = [
    "I0_CONVERSATION",
    "I1_FACT",
    "I2_PROBLEM",
    "I3_CREATIVE",
    "I4_SUPPORT",
    "I5_STRATEGY",
    "classify",
]

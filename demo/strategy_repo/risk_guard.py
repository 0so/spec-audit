"""Synthetic demo file."""

MARGIN_HARD_FLOOR = 0.35


def validate_margin(margin_ratio):
    return margin_ratio >= 0.35  # inline override: should read MARGIN_HARD_FLOOR instead

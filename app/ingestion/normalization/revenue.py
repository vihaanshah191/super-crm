"""INR revenue normalization.

Internal representation is always plain numeric INR (a value, or a
range_min/range_max pair). Lakh/crore formatting is a presentation-layer
concern only -- see docs/entity_resolution.md for the rationale.

  1 lakh = 100,000
  1 crore = 10,000,000
"""

import re
from dataclasses import dataclass

LAKH = 100_000
CRORE = 10_000_000

_UNIT_MULTIPLIERS = {
    "crore": CRORE,
    "crores": CRORE,
    "cr": CRORE,
    "lakh": LAKH,
    "lakhs": LAKH,
    "lac": LAKH,
    "lacs": LAKH,
    "million": 1_000_000,
    "mn": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "thousand": 1_000,
    "k": 1_000,
}

_NUMBER = r"[\d,]+(?:\.\d+)?"
_UNIT = r"(crores?|cr|lakhs?|lacs?|million|mn|m|billion|bn|thousand|k)?"

_RANGE_PATTERN = re.compile(
    rf"(?:₹|rs\.?|inr)?\s*({_NUMBER})\s*{_UNIT}?\s*[-to]{{1,3}}\s*({_NUMBER})\s*{_UNIT}?",
    re.IGNORECASE,
)
_SINGLE_PATTERN = re.compile(
    rf"(?:₹|rs\.?|inr)?\s*({_NUMBER})\s*{_UNIT}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RevenueValue:
    value_inr: float | None
    range_min_inr: float | None
    range_max_inr: float | None
    unit: str = "INR"


def _to_number(raw: str) -> float:
    return float(raw.replace(",", ""))


def _unit_multiplier(unit: str | None) -> int:
    if not unit:
        return 1
    return _UNIT_MULTIPLIERS.get(unit.lower(), 1)


def parse_inr_revenue(text: str) -> RevenueValue | None:
    """Parse a free-text revenue string into standardized INR numeric value(s).

    Examples:
      "₹10 crore"        -> value 100_000_000
      "10-50 crore"       -> range (100_000_000, 500_000_000)
      "128000000"         -> value 128_000_000
      "Rs. 1.2 lakh"      -> value 120_000
    Returns None if the text cannot be parsed as a monetary amount.
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    range_match = _RANGE_PATTERN.search(text)
    if range_match:
        low_raw, high_raw = range_match.group(1), range_match.group(3)
        units_found = re.findall(
            r"crores?|cr|lakhs?|lacs?|million|mn|m|billion|bn|thousand|k", text, re.IGNORECASE
        )
        shared_unit = units_found[-1] if units_found else None
        low = _to_number(low_raw) * _unit_multiplier(shared_unit)
        high = _to_number(high_raw) * _unit_multiplier(shared_unit)
        if low > high:
            low, high = high, low
        return RevenueValue(value_inr=None, range_min_inr=low, range_max_inr=high)

    single_match = _SINGLE_PATTERN.search(text)
    if single_match:
        number_raw, unit = single_match.group(1), single_match.group(2)
        value = _to_number(number_raw) * _unit_multiplier(unit)
        return RevenueValue(value_inr=value, range_min_inr=None, range_max_inr=None)

    return None

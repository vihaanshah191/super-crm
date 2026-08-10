"""Employee count / range normalization.

Handles free-text forms like "50-200 employees", "20+", "~34 employees",
"1000+ employees" and plain integers.
"""

import re
from dataclasses import dataclass

_RANGE_PATTERN = re.compile(r"(\d[\d,]*)\s*[-to]{1,3}\s*(\d[\d,]*)")
_PLUS_PATTERN = re.compile(r"(\d[\d,]*)\s*\+")
_SINGLE_PATTERN = re.compile(r"(\d[\d,]*)")


@dataclass(frozen=True)
class EmployeeRangeValue:
    count: int | None
    range_min: int | None
    range_max: int | None


def _to_int(raw: str) -> int:
    return int(raw.replace(",", ""))


def parse_employee_range(text: str) -> EmployeeRangeValue | None:
    """
    "34"                  -> count=34
    "50-200 employees"    -> range_min=50, range_max=200
    "1000+ employees"     -> range_min=1000, range_max=None
    """
    if not text or not text.strip():
        return None
    text = text.strip()

    range_match = _RANGE_PATTERN.search(text)
    if range_match:
        low, high = _to_int(range_match.group(1)), _to_int(range_match.group(2))
        if low > high:
            low, high = high, low
        return EmployeeRangeValue(count=None, range_min=low, range_max=high)

    plus_match = _PLUS_PATTERN.search(text)
    if plus_match:
        low = _to_int(plus_match.group(1))
        return EmployeeRangeValue(count=None, range_min=low, range_max=None)

    single_match = _SINGLE_PATTERN.search(text)
    if single_match:
        value = _to_int(single_match.group(1))
        return EmployeeRangeValue(count=value, range_min=value, range_max=value)

    return None

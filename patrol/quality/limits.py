from dataclasses import dataclass

from patrol import InvalidInput


@dataclass(frozen=True, slots=True)
class LimitAssessment:
    limit: int
    difference: int
    exceeded: bool


def assess_peak_limit(peak: int, frozen_limit: int) -> LimitAssessment:
    if type(peak) is not int or type(frozen_limit) is not int:
        raise InvalidInput("peak and frozen limit must be integers in the same unit")
    difference = peak - frozen_limit
    return LimitAssessment(frozen_limit, difference, difference > 0)

from dataclasses import dataclass

from patrol import InvalidInput
from patrol.quality.limits import assess_peak_limit
from patrol.quality.peaks import find_first_peak
from patrol.quality.units import validate_measurements
from patrol.source.freeze import SourceFrame


@dataclass(frozen=True, slots=True)
class PeakResult:
    frame_key: str
    unit: str
    value: int
    position: int
    limit: int
    difference: int
    exceeded: bool


def evaluate_frame(frame: SourceFrame) -> PeakResult:
    if not isinstance(frame, SourceFrame):
        raise InvalidInput("peak calculation requires a frozen source frame")
    series = validate_measurements(frame.settings, frame.words)
    peak = find_first_peak(series.positioned())
    assessment = assess_peak_limit(peak.value, frame.settings.peak_limit)
    return PeakResult(
        frame.frame_key, series.unit, peak.value, peak.position,
        assessment.limit, assessment.difference, assessment.exceeded,
    )

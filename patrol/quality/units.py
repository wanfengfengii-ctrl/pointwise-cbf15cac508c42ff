from collections.abc import Iterator
from dataclasses import dataclass

from patrol import InvalidInput
from patrol.runs.settings import SamplingSettings


@dataclass(frozen=True, slots=True)
class MeasurementSeries:
    unit: str
    words: tuple[int, ...]

    def positioned(self) -> Iterator[tuple[int, int]]:
        return enumerate(self.words, 1)


def validate_measurements(settings: SamplingSettings, words: tuple[int, ...]) -> MeasurementSeries:
    if not isinstance(settings, SamplingSettings):
        raise InvalidInput("measurement requires frozen settings in the original unit")
    if not isinstance(words, tuple) or not words:
        raise InvalidInput("measurement requires nonempty ordered words")
    for position, word in enumerate(words, 1):
        if not settings.accepts_word(word):
            raise InvalidInput(f"illegal measurement at physical position {position}")
    return MeasurementSeries(settings.unit, words)

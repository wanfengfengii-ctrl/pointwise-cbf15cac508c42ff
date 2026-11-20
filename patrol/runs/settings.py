from collections.abc import Mapping
from dataclasses import dataclass

from patrol import InvalidInput, require_text


@dataclass(frozen=True, slots=True)
class SamplingSettings:
    unit: str
    minimum: int
    maximum: int
    peak_limit: int

    def __post_init__(self) -> None:
        require_text(self.unit, "sample unit")
        for name in ("minimum", "maximum", "peak_limit"):
            if type(getattr(self, name)) is not int:
                raise InvalidInput(f"{name} must be an integer")
        if self.minimum > self.maximum:
            raise InvalidInput("sample range is reversed")

    @property
    def word_width(self) -> int:
        low = ~self.minimum if self.minimum < 0 else self.minimum
        high = ~self.maximum if self.maximum < 0 else self.maximum
        return (max(low.bit_length(), high.bit_length()) + 8) // 8

    def accepts_word(self, word: object) -> bool:
        return type(word) is int and self.minimum <= word <= self.maximum


def freeze_settings(request: SamplingSettings | Mapping[str, object]) -> SamplingSettings:
    if isinstance(request, SamplingSettings):
        return request
    if not isinstance(request, Mapping):
        raise InvalidInput("sampling settings must be given as a settings object or mapping")
    values = dict(request)
    if values.keys() != {"unit", "minimum", "maximum", "peak_limit"}:
        raise InvalidInput("sampling settings require unit, minimum, maximum and peak_limit")
    return SamplingSettings(**values)

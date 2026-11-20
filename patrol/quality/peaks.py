from collections.abc import Iterable
from dataclasses import dataclass

from patrol import InvalidInput


@dataclass(frozen=True, slots=True)
class Peak:
    value: int
    position: int


def find_first_peak(positioned: Iterable[tuple[int, int]]) -> Peak:
    readings = iter(positioned)
    try:
        best_position, best_word = next(readings)
    except StopIteration as exc:
        raise InvalidInput("cannot find a peak in an empty measurement series") from exc
    for position, word in readings:
        if word > best_word:
            best_position, best_word = position, word
    return Peak(best_word, best_position)

from patrol import InvalidInput, SequenceError
from patrol.runs.catalog import RunCatalog
from patrol.source.slots import SlotBank


def record_word(runs: RunCatalog, slots: SlotBank, run_id: str, position: int, word: int) -> int:
    with runs.lock, slots.lock:
        run = runs.require_active(run_id)
        expected = slots.next_position(run_id)
        if type(position) is not int or position != expected:
            raise SequenceError(f"expected physical position {expected}")
        if not run.settings.accepts_word(word):
            raise InvalidInput(f"word at physical position {position} violates frozen sampling settings")
        slots.claim(run)
        return slots.append_word(run_id, word)

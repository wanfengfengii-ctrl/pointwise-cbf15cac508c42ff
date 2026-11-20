"""Domain errors and shared text validation for the patrol station."""


class PatrolError(Exception):
    """Base error for a rejected patrol operation."""


class InvalidInput(PatrolError):
    """Malformed input or a word outside its frozen range."""


class NotFound(PatrolError):
    """The requested record does not exist."""


class Conflict(PatrolError):
    """A stable identity already has different data."""


class SequenceError(PatrolError):
    """A sample did not arrive at the next physical position."""


class StateError(PatrolError):
    """The current run or slot state prevents the operation."""


class CapacityError(PatrolError):
    """The requested object exceeds the target medium's free words."""


class MediumUnavailable(PatrolError):
    """The selected medium is offline or cannot be read."""


class MediaWriteError(PatrolError):
    """A candidate could not be written or published."""


class StorageError(PatrolError):
    """Station metadata could not be read or written."""


class CommitError(StorageError):
    """The archive transaction could not be committed."""


class ReleaseDenied(PatrolError):
    """The receipt does not authorize clearing the current frame."""


class CloseoutDenied(PatrolError):
    """The receipt cannot fix this run's shift manifest."""


class AuditError(PatrolError):
    """A known receipt could not be verified against its original evidence."""


def require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise InvalidInput(f"{label} must be nonempty text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InvalidInput(f"{label} must be valid UTF-8 text") from exc
    return value

import hashlib
import json
from dataclasses import asdict

from patrol import AuditError, InvalidInput
from patrol.runs.settings import SamplingSettings
from patrol.source.freeze import SourceFrame
from patrol.source.origin import FrameOrigin


def frame_context(origin: FrameOrigin, settings: SamplingSettings) -> str:
    origin_fields = asdict(origin)
    origin_fields["generation"] = hex(origin.generation)
    return json.dumps(
        {
            "origin": origin_fields,
            "settings": {
                "unit": settings.unit,
                "minimum": hex(settings.minimum),
                "maximum": hex(settings.maximum),
                "peak_limit": hex(settings.peak_limit),
            },
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def load_frame_context(context: str) -> tuple[FrameOrigin, SamplingSettings]:
    try:
        document = json.loads(context)
        origin_fields = document["origin"]
        settings_fields = document["settings"]
        origin_fields["generation"] = int(origin_fields["generation"], 16)
        for field in ("minimum", "maximum", "peak_limit"):
            settings_fields[field] = int(settings_fields[field], 16)
        origin = FrameOrigin(**origin_fields)
        settings = SamplingSettings(**settings_fields)
    except (InvalidInput, KeyError, TypeError, ValueError) as exc:
        raise AuditError("malformed frozen archive context") from exc
    if frame_context(origin, settings) != context:
        raise AuditError("noncanonical frozen archive context")
    return origin, settings


def encode_whole_frame(frame: SourceFrame) -> bytes:
    if not isinstance(frame, SourceFrame):
        raise InvalidInput("whole-frame archive requires a frozen source frame")
    width = frame.settings.word_width
    return b"".join(word.to_bytes(width, "big", signed=True) for word in frame.words)


def decode_whole_frame(
    origin: FrameOrigin,
    settings: SamplingSettings,
    word_count: int,
    word_width: int,
    payload: bytes,
) -> SourceFrame:
    if type(word_count) is not int or word_count < 1:
        raise AuditError("archive word count must be positive")
    if type(word_width) is not int or word_width != settings.word_width:
        raise AuditError("archive word width differs from the frozen sample width")
    if len(payload) != word_count * word_width:
        raise AuditError("archive payload length differs from its fixed word count")
    words = tuple(
        int.from_bytes(payload[offset:offset + word_width], "big", signed=True)
        for offset in range(0, len(payload), word_width)
    )
    try:
        return SourceFrame(origin, settings, words)
    except InvalidInput as exc:
        raise AuditError("archive contains words outside the frozen sample range") from exc


def frame_digest(frame: SourceFrame, payload: bytes | None = None) -> str:
    if payload is None:
        payload = encode_whole_frame(frame)
    digest = hashlib.sha256()
    digest.update(b"patrol-whole-frame-v1\x00")
    digest.update(frame_context(frame.origin, frame.settings).encode("ascii"))
    digest.update(b"\x00")
    digest.update(payload)
    return digest.hexdigest()

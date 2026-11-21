"""Capacity-limited takeover archiving shared across the three station media.

A frozen frame payload is encoded into three fragments — a leading share, a
trailing share and their parity — so that any two media rebuild the original
payload exactly.  The SQLite catalog only keeps the order binding, fragment
positioning, digests and the frozen measurements; every byte needed to
rebuild the frame lives in a managed medium data area and is billed to that
medium at the frozen word width.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from sqlite3 import Connection
from uuid import uuid4

from patrol import (
    AuditError,
    CapacityError,
    InvalidInput,
    MediaWriteError,
    MediumUnavailable,
    NotFound,
    StorageError,
)
from patrol.archive.codec import (
    decode_whole_frame,
    encode_whole_frame,
    frame_context,
    load_frame_context,
)
from patrol.archive.transactions import ArchiveStore
from patrol.quality.results import PeakResult
from patrol.runs.settings import SamplingSettings
from patrol.source.freeze import SourceFrame
from patrol.source.origin import FrameOrigin

_OBJECT_ID = re.compile(r"[0-9a-f]{32}")
_RECEIPT_ID = re.compile(r"takeover-[0-9a-f]{32}")
_DIGEST = re.compile(r"[0-9a-f]{64}")

LEADING, TRAILING, PARITY = 0, 1, 2
ROLES = (LEADING, TRAILING, PARITY)


@dataclass(frozen=True, slots=True)
class FragmentPlan:
    """Fixed placement and billing of one encoded share on one medium."""

    role: int
    object_id: str
    volume_id: str
    byte_count: int
    word_count: int
    digest: str


@dataclass(frozen=True, slots=True)
class TakeoverOrder:
    """The accepted order binding: frame identity, evidence and fragment plan."""

    order_id: str
    takeover_id: str
    origin: FrameOrigin
    settings: SamplingSettings
    digest: str
    word_count: int
    word_width: int
    split_bytes: int
    plan: tuple[FragmentPlan, ...]
    result: PeakResult
    committed: bool

    @property
    def frame_key(self) -> str:
        return self.origin.frame_key

    @property
    def receipt_id(self) -> str:
        return f"takeover-{self.takeover_id}"


@dataclass(frozen=True, slots=True)
class TakeoverReceipt:
    receipt_id: str
    order_id: str
    origin: FrameOrigin
    settings: SamplingSettings
    digest: str
    result: PeakResult

    @property
    def frame_key(self) -> str:
        return self.origin.frame_key


def takeover_receipt(order: TakeoverOrder) -> TakeoverReceipt:
    return TakeoverReceipt(
        order.receipt_id, order.order_id, order.origin, order.settings, order.digest, order.result,
    )


def takeover_frame_digest(frame: SourceFrame, payload: bytes | None = None) -> str:
    if payload is None:
        payload = encode_whole_frame(frame)
    digest = hashlib.sha256()
    digest.update(b"patrol-takeover-frame-v1\x00")
    digest.update(frame_context(frame.origin, frame.settings).encode("ascii"))
    digest.update(b"\x00")
    digest.update(payload)
    return digest.hexdigest()


def takeover_fragment_digest(order_digest: str, role: int, payload: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(b"patrol-takeover-fragment-v1\x00")
    digest.update(order_digest.encode("ascii"))
    digest.update(b"\x00")
    digest.update(str(role).encode("ascii"))
    digest.update(b"\x00")
    digest.update(payload)
    return digest.hexdigest()


def encode_takeover_fragments(payload: bytes, split: int) -> tuple[bytes, bytes, bytes]:
    if type(split) is not int or not 0 <= split <= len(payload):
        raise InvalidInput("takeover split must stay inside the encoded frame")
    leading, trailing = payload[:split], payload[split:]
    size = max(len(leading), len(trailing))
    parity = bytes(
        left ^ right
        for left, right in zip(leading.ljust(size, b"\x00"), trailing.ljust(size, b"\x00"))
    )
    return leading, trailing, parity


def _xor_share(parity: bytes, other: bytes, length: int) -> bytes:
    if len(parity) < length:
        raise AuditError("takeover parity share is shorter than its fixed length")
    return bytes(
        parity[index] ^ (other[index] if index < len(other) else 0)
        for index in range(length)
    )


def reconstruct_takeover_payload(total_bytes: int, split: int, fragments: dict[int, bytes]) -> bytes:
    leading, trailing, parity = (fragments.get(role) for role in ROLES)
    if leading is not None and trailing is not None:
        payload = leading + trailing
    elif leading is not None and parity is not None:
        payload = leading + _xor_share(parity, leading, total_bytes - split)
    elif trailing is not None and parity is not None:
        payload = _xor_share(parity, trailing, split) + trailing
    else:
        raise AuditError("surviving takeover fragments cannot rebuild the original frame")
    if len(payload) != total_bytes:
        raise AuditError("rebuilt takeover payload differs from the fixed frame length")
    return payload


def plan_takeover(
    store: ArchiveStore,
    db: Connection,
    frame: SourceFrame,
    payload: bytes,
    order_digest: str,
) -> tuple[int, tuple[FragmentPlan, ...]]:
    """Choose the split and medium assignment from the actual net remaining words."""
    word_count = len(frame.words)
    word_width = frame.settings.word_width
    ranked = sorted(
        (
            (spec.name, store.volumes.available_words(spec.name, db=db))
            for spec in store.volumes.specifications(db=db)
        ),
        key=lambda item: item[1],
    )
    smallest_pair = ranked[0][1] + ranked[1][1]
    if smallest_pair < word_count:
        raise CapacityError(
            f"takeover of a {word_count}-word frame needs two media sparing {word_count} words, "
            f"but the smallest pair spares {smallest_pair}",
        )
    total_bytes = word_count * word_width
    split = min(ranked[0][1] * word_width, total_bytes)
    shares = encode_takeover_fragments(payload, split)
    plan = []
    for role, ((volume_id, free_words), share) in enumerate(zip(ranked, shares, strict=True)):
        share_words = -(-len(share) // word_width)
        if share_words > free_words:
            raise CapacityError(
                f"medium {volume_id!r} has {free_words} free words for a {share_words}-word share",
            )
        plan.append(FragmentPlan(
            role, uuid4().hex, volume_id, len(share), share_words,
            takeover_fragment_digest(order_digest, role, share),
        ))
    return split, tuple(plan)


def _encode_plan(plan: tuple[FragmentPlan, ...]) -> str:
    return json.dumps(
        {"fragments": [asdict(entry) for entry in plan]},
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_plan(text: str) -> tuple[FragmentPlan, ...]:
    try:
        document = json.loads(text)
        plan = tuple(
            FragmentPlan(
                role=entry["role"],
                object_id=entry["object_id"],
                volume_id=entry["volume_id"],
                byte_count=entry["byte_count"],
                word_count=entry["word_count"],
                digest=entry["digest"],
            )
            for entry in document["fragments"]
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise AuditError("malformed takeover plan in the station catalog") from exc
    if sorted(entry.role for entry in plan) != [0, 1, 2]:
        raise AuditError("takeover plan does not fix all three fragment roles")
    for entry in plan:
        if not isinstance(entry.object_id, str) or _OBJECT_ID.fullmatch(entry.object_id) is None:
            raise AuditError("takeover plan carries a malformed object identifier")
        if not isinstance(entry.volume_id, str) or not entry.volume_id:
            raise AuditError("takeover plan carries a malformed medium name")
        if type(entry.byte_count) is not int or entry.byte_count < 0:
            raise AuditError("takeover plan carries a malformed share length")
        if type(entry.word_count) is not int or entry.word_count < 0:
            raise AuditError("takeover plan carries a malformed word billing")
        if not isinstance(entry.digest, str) or _DIGEST.fullmatch(entry.digest) is None:
            raise AuditError("takeover plan carries a malformed fragment digest")
    return plan


def _check_plan(plan: tuple[FragmentPlan, ...], word_count: int, word_width: int, split: int) -> None:
    total_bytes = word_count * word_width
    expected = {
        LEADING: split,
        TRAILING: total_bytes - split,
        PARITY: max(split, total_bytes - split),
    }
    for entry in plan:
        if entry.byte_count != expected[entry.role]:
            raise AuditError("takeover plan share differs from the fixed frame split")
        if entry.word_count != -(-entry.byte_count // word_width):
            raise AuditError("takeover plan billing differs from its fixed share size")


def _decode_order(row) -> TakeoverOrder:
    origin, settings = load_frame_context(row["context"])
    if row["frame_key"] != origin.frame_key:
        raise AuditError("takeover order identity differs from its frozen context")
    takeover_id = row["takeover_id"]
    if not isinstance(takeover_id, str) or _OBJECT_ID.fullmatch(takeover_id) is None:
        raise AuditError("takeover order carries a malformed takeover identifier")
    digest = row["digest"]
    if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
        raise AuditError("takeover order carries a malformed frame digest")
    word_count = row["word_count"]
    word_width = row["word_width"]
    split = row["split_bytes"]
    if type(word_count) is not int or word_count < 1:
        raise AuditError("takeover order carries a malformed word count")
    if type(word_width) is not int or word_width != settings.word_width:
        raise AuditError("takeover word width differs from the frozen sample width")
    if type(split) is not int or not 0 <= split <= word_count * word_width:
        raise AuditError("takeover split falls outside the fixed frame payload")
    plan = _decode_plan(row["plan"])
    _check_plan(plan, word_count, word_width, split)
    position = row["peak_position"]
    exceeded = row["exceeded"]
    if type(position) is not int or position < 1 or type(exceeded) is not int or exceeded not in (0, 1):
        raise AuditError("takeover order contains a malformed peak result")
    try:
        result = PeakResult(
            frame_key=origin.frame_key,
            unit=settings.unit,
            value=int(row["peak_value"], 16),
            position=position,
            limit=settings.peak_limit,
            difference=int(row["difference"], 16),
            exceeded=bool(exceeded),
        )
    except (TypeError, ValueError) as exc:
        raise AuditError("takeover order contains malformed integer measurements") from exc
    committed = row["committed"]
    if type(committed) is not int or committed not in (0, 1):
        raise AuditError("takeover order carries a malformed commit state")
    return TakeoverOrder(
        row["order_id"], takeover_id, origin, settings, digest,
        word_count, word_width, split, plan, result, bool(committed),
    )


class TakeoverRegistry:
    def __init__(self, store: ArchiveStore) -> None:
        self._store = store

    @staticmethod
    def initialize(db: Connection) -> None:
        db.execute("""
            CREATE TABLE IF NOT EXISTS takeover_orders (
                order_id TEXT PRIMARY KEY,
                takeover_id TEXT NOT NULL UNIQUE,
                frame_key TEXT NOT NULL UNIQUE,
                context TEXT NOT NULL,
                digest TEXT NOT NULL,
                word_count INTEGER NOT NULL CHECK (word_count > 0),
                word_width INTEGER NOT NULL CHECK (word_width > 0),
                split_bytes INTEGER NOT NULL CHECK (split_bytes >= 0),
                plan TEXT NOT NULL,
                peak_value TEXT NOT NULL,
                peak_position INTEGER NOT NULL CHECK (peak_position > 0),
                difference TEXT NOT NULL,
                exceeded INTEGER NOT NULL CHECK (exceeded IN (0, 1)),
                committed INTEGER NOT NULL CHECK (committed IN (0, 1)) DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS takeover_fragments (
                object_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL REFERENCES takeover_orders(order_id),
                role INTEGER NOT NULL CHECK (role IN (0, 1, 2)),
                volume_id TEXT NOT NULL REFERENCES volumes(name),
                byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
                word_count INTEGER NOT NULL CHECK (word_count >= 0),
                digest TEXT NOT NULL,
                UNIQUE (order_id, role)
            )
        """)

    def order_by_id(self, order_id: str, *, db: Connection) -> TakeoverOrder | None:
        row = db.execute(
            "SELECT * FROM takeover_orders WHERE order_id = ?", (order_id,),
        ).fetchone()
        return None if row is None else _decode_order(row)

    def order_by_frame(self, frame_key: str, *, db: Connection) -> TakeoverOrder | None:
        row = db.execute(
            "SELECT * FROM takeover_orders WHERE frame_key = ?", (frame_key,),
        ).fetchone()
        return None if row is None else _decode_order(row)

    def record_order(
        self,
        db: Connection,
        order_id: str,
        frame: SourceFrame,
        digest: str,
        result: PeakResult,
        split: int,
        plan: tuple[FragmentPlan, ...],
    ) -> TakeoverOrder:
        settings = frame.settings
        if (
            result.frame_key != frame.frame_key
            or result.unit != settings.unit
            or result.limit != settings.peak_limit
        ):
            raise InvalidInput("peak result does not refer to the source frame's frozen context")
        takeover_id = uuid4().hex
        db.execute("""
            INSERT INTO takeover_orders(
                order_id, takeover_id, frame_key, context, digest,
                word_count, word_width, split_bytes, plan,
                peak_value, peak_position, difference, exceeded, committed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            order_id, takeover_id, frame.frame_key, frame_context(frame.origin, settings),
            digest, len(frame.words), settings.word_width, split, _encode_plan(plan),
            hex(result.value), result.position, hex(result.difference), int(result.exceeded),
        ))
        return TakeoverOrder(
            order_id, takeover_id, frame.origin, settings, digest,
            len(frame.words), settings.word_width, split, plan, result, False,
        )

    def fragment_committed(self, object_id: str, *, db: Connection) -> bool:
        return db.execute(
            "SELECT 1 FROM takeover_fragments WHERE object_id = ?", (object_id,),
        ).fetchone() is not None

    def record_fragment(self, db: Connection, order_id: str, entry: FragmentPlan) -> None:
        db.execute("""
            INSERT INTO takeover_fragments(object_id, order_id, role, volume_id, byte_count, word_count, digest)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.object_id, order_id, entry.role, entry.volume_id,
            entry.byte_count, entry.word_count, entry.digest,
        ))

    def fragments_for(self, order_id: str, *, db: Connection) -> tuple[FragmentPlan, ...]:
        rows = db.execute("""
            SELECT role, object_id, volume_id, byte_count, word_count, digest
            FROM takeover_fragments WHERE order_id = ? ORDER BY role
        """, (order_id,)).fetchall()
        return tuple(
            FragmentPlan(
                row["role"], row["object_id"], row["volume_id"],
                row["byte_count"], row["word_count"], row["digest"],
            )
            for row in rows
        )

    def mark_committed(self, db: Connection, order_id: str) -> None:
        db.execute("UPDATE takeover_orders SET committed = 1 WHERE order_id = ?", (order_id,))

    def get_committed_order(self, receipt_id: str, *, db: Connection | None = None) -> TakeoverOrder | None:
        if not isinstance(receipt_id, str) or _RECEIPT_ID.fullmatch(receipt_id) is None:
            return None
        if db is None:
            with self._store.read_transaction() as reader:
                return self.get_committed_order(receipt_id, db=reader)
        row = db.execute(
            "SELECT * FROM takeover_orders WHERE takeover_id = ? AND committed = 1",
            (receipt_id.removeprefix("takeover-"),),
        ).fetchone()
        return None if row is None else _decode_order(row)


def stage_takeover_fragments(store: ArchiveStore, order: TakeoverOrder, payload: bytes) -> None:
    """Write planned shares to their media and register the actually held fragments."""
    registry = TakeoverRegistry(store)
    shares = encode_takeover_fragments(payload, order.split_bytes)
    try:
        with store.write_transaction() as db:
            for entry in order.plan:
                if registry.fragment_committed(entry.object_id, db=db):
                    continue
                share = shares[entry.role]
                if (
                    len(share) != entry.byte_count
                    or takeover_fragment_digest(order.digest, entry.role, share) != entry.digest
                ):
                    raise MediaWriteError("planned takeover share differs from the fixed order evidence")
                medium = store.volumes.medium(entry.volume_id, db=db)
                medium.require_online()
                medium.stage_object(entry.object_id, share)
                medium.publish_object(entry.object_id)
                if medium.read_object(entry.object_id) != share:
                    raise MediaWriteError("published takeover fragment did not read back as planned")
                registry.record_fragment(db, order.order_id, entry)
    except BaseException:
        discard_uncommitted_fragments(store, order)
        raise


def discard_uncommitted_fragments(store: ArchiveStore, order: TakeoverOrder) -> None:
    registry = TakeoverRegistry(store)
    for entry in order.plan:
        try:
            with store.write_transaction() as db:
                if registry.fragment_committed(entry.object_id, db=db):
                    continue
                store.volumes.medium(entry.volume_id, db=db).discard_candidate(entry.object_id)
        except (StorageError, MediumUnavailable, MediaWriteError):
            # Leave leftovers for the reopen cleanup when metadata or media is unavailable.
            continue


def verify_takeover_recovery(store: ArchiveStore, order: TakeoverOrder) -> None:
    """Re-read persisted fragments and prove masking any one medium keeps the frame."""
    held: dict[int, bytes] = {}
    for entry in order.plan:
        medium = store.volumes.medium(entry.volume_id)
        data = medium.read_object(entry.object_id)
        if (
            len(data) != entry.byte_count
            or takeover_fragment_digest(order.digest, entry.role, data) != entry.digest
        ):
            raise MediaWriteError("persisted takeover fragment differs from the fixed order evidence")
        held[entry.role] = data
    total_bytes = order.word_count * order.word_width
    for masked in ROLES:
        surviving = {role: data for role, data in held.items() if role != masked}
        try:
            payload = reconstruct_takeover_payload(total_bytes, order.split_bytes, surviving)
            frame = decode_whole_frame(
                order.origin, order.settings, order.word_count, order.word_width, payload,
            )
        except AuditError as exc:
            raise MediaWriteError("masking one medium loses the fixed takeover frame") from exc
        if takeover_frame_digest(frame, payload) != order.digest:
            raise MediaWriteError("masked takeover recovery differs from the fixed frame evidence")


def recover_takeover_frame(store: ArchiveStore, db: Connection, order: TakeoverOrder) -> SourceFrame:
    """Rebuild the fixed frame from surviving media, doubting any failing fragment."""
    registry = TakeoverRegistry(store)
    planned = {entry.role: entry for entry in order.plan}
    held: dict[int, bytes] = {}
    for stored in registry.fragments_for(order.order_id, db=db):
        entry = planned.get(stored.role)
        if entry is None or stored != entry:
            # Catalog rows that differ from the fixed plan cannot serve as evidence.
            continue
        try:
            data = store.volumes.medium(entry.volume_id, db=db).read_object(entry.object_id)
        except (InvalidInput, MediumUnavailable, NotFound):
            # A medium that fails the original evidence checks counts as unreadable.
            continue
        if len(data) != entry.byte_count:
            continue
        if takeover_fragment_digest(order.digest, entry.role, data) != entry.digest:
            continue
        held[entry.role] = data
    payload = reconstruct_takeover_payload(order.word_count * order.word_width, order.split_bytes, held)
    frame = decode_whole_frame(order.origin, order.settings, order.word_count, order.word_width, payload)
    if takeover_frame_digest(frame, payload) != order.digest:
        raise AuditError("recovered takeover frame differs from the fixed order evidence")
    return frame

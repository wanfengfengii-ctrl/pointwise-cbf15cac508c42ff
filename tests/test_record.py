import unittest

from patrol import Conflict, InvalidInput, NotFound, SequenceError, StateError
from patrol.runs.catalog import RunCatalog
from patrol.runs.lifecycle import open_run
from patrol.runs.points import PointRegistry
from patrol.source.freeze import freeze_frame, read_frozen_frame
from patrol.source.samples import record_word
from patrol.source.slots import SlotBank
from tests import SETTINGS


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.points = PointRegistry()
        self.points.register("P07", "东湾海堤堤顶")
        self.runs = RunCatalog()
        self.slots = SlotBank("DWB-01", "bay-1", session_id="test-shift")

    def open(self, settings=SETTINGS):
        return open_run(self.points, self.runs, "D01", "P07", settings)

    def word(self, position, value):
        return record_word(self.runs, self.slots, "D01", position, value)

    def stop(self):
        return freeze_frame(self.runs, self.slots, "D01")

    def test_freeze_preserves_device_order(self):
        self.open()
        for position, word in enumerate((4, 9, 2), 1):
            self.assertEqual(self.word(position, word), position)
        frame = self.stop()
        self.assertEqual(frame.words, (4, 9, 2))
        self.assertEqual(frame.origin.point_id, "P07")
        self.assertEqual(frame.origin.run_id, "D01")
        self.assertEqual(frame.origin.generation, 1)
        self.assertEqual(read_frozen_frame(self.slots, frame.frame_key), frame)
        self.assertEqual(self.runs.find("D01").frozen_frame_key, frame.frame_key)

    def test_open_contract(self):
        with self.assertRaises(NotFound):
            open_run(self.points, self.runs, "D01", "unknown", SETTINGS)
        invalid = dict(unit="mm/s", minimum=5, maximum=4, peak_limit=9)
        for changes in ({}, {"minimum": -12, "unit": ""}, {"minimum": -12, "peak_limit": True}):
            with self.subTest(changes=changes), self.assertRaises(InvalidInput):
                self.open(invalid | changes)
        self.assertIsNone(self.runs.find("D01"))
        first = self.open()
        self.assertIs(self.open(), first)
        with self.assertRaises(Conflict):
            self.open(dict(unit="mm/s", minimum=-12, maximum=12, peak_limit=7))
        with self.assertRaises(Conflict):
            self.points.register("P07", "另一个堤顶")
        self.assertIs(self.runs.find("D01"), first)
        self.assertEqual(self.points.require("P07").location, "东湾海堤堤顶")

    def test_frozen_settings(self):
        request = dict(unit="mm/s", minimum=-12, maximum=12, peak_limit=9)
        self.open(request)
        request.update(maximum=5, peak_limit=5, unit="cm/s")
        for position, word in enumerate((4, 9, 2), 1):
            self.word(position, word)
        frame = self.stop()
        self.assertEqual(frame.words, (4, 9, 2))
        self.assertEqual((frame.settings.unit, frame.settings.minimum, frame.settings.maximum), ("mm/s", -12, 12))
        self.assertEqual(frame.settings.peak_limit, 9)

    def test_empty_freeze(self):
        self.open()
        with self.assertRaises(StateError):
            self.stop()
        self.assertFalse(self.runs.find("D01").stopped)
        self.assertEqual(self.word(1, 4), 1)
        self.assertEqual(self.stop().words, (4,))

    def test_rejected_word_keeps_position(self):
        self.open()
        self.word(1, 4)
        with self.assertRaises(InvalidInput):
            self.word(2, 99)
        self.assertEqual(self.word(2, 9), 2)
        self.assertEqual(self.word(3, 2), 3)
        self.assertEqual(self.stop().words, (4, 9, 2))

    def test_sequence_and_type(self):
        self.open()
        for position in (2, True, 1.0, 0):
            with self.subTest(position=position), self.assertRaises(SequenceError):
                self.word(position, 4)
        for value in (None, True, 4.0, "4"):
            with self.subTest(value=value), self.assertRaises(InvalidInput):
                self.word(1, value)
        self.assertEqual(self.word(1, -12), 1)
        self.assertEqual(self.word(2, 12), 2)
        self.assertEqual(self.stop().words, (-12, 12))

    def test_after_stop(self):
        self.open()
        self.word(1, 4)
        frozen = self.stop()
        with self.assertRaises(StateError):
            self.word(2, 9)
        with self.assertRaises(StateError):
            self.stop()
        self.assertEqual(self.open().frozen_frame_key, frozen.frame_key)
        self.assertEqual(read_frozen_frame(self.slots, frozen.frame_key).words, (4,))

    def test_busy_slot_preserves_first_run(self):
        self.open()
        self.word(1, 4)
        open_run(self.points, self.runs, "D02", "P07", SETTINGS)
        with self.assertRaises(StateError):
            record_word(self.runs, self.slots, "D02", 1, 7)
        with self.assertRaises(StateError):
            freeze_frame(self.runs, self.slots, "D02")
        with self.assertRaises(StateError):
            self.slots.close()
        self.assertEqual(self.word(2, 9), 2)
        self.assertEqual(self.stop().words, (4, 9))
        self.assertFalse(self.runs.find("D02").stopped)

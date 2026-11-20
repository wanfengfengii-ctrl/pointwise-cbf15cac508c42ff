"""Deterministic fixtures shared by tests of the receiving station."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from patrol.archive.intake import archive_frame
from patrol.archive.transactions import ArchiveStore
from patrol.archive.volumes import VolumeSpec
from patrol.runs.catalog import RunCatalog
from patrol.runs.lifecycle import open_run
from patrol.runs.points import PointRegistry
from patrol.runs.settings import SamplingSettings
from patrol.source.freeze import freeze_frame
from patrol.source.release import release_frame
from patrol.source.samples import record_word
from patrol.source.slots import SlotBank

SETTINGS = SamplingSettings("mm/s", -12, 12, 9)


class StationCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="patrol-test-")))
        self.points = PointRegistry()
        self.points.register("P07", "东湾海堤堤顶")
        self.runs = RunCatalog()
        self.slots = SlotBank("DWB-01", "bay-1", session_id="test-shift")

    def open_station(self, capacities=(8, 6, 5), **options):
        specs = tuple(VolumeSpec(name, capacity) for name, capacity in zip(
            ("east", "middle", "west"), capacities, strict=True,
        ))
        return ArchiveStore.open(self.root, specs, **options)

    def frame(self, run_id, words, settings=SETTINGS):
        open_run(self.points, self.runs, run_id, "P07", settings)
        for position, word in enumerate(words, 1):
            self.assertEqual(record_word(self.runs, self.slots, run_id, position, word), position)
        return freeze_frame(self.runs, self.slots, run_id)

    def archive_and_release(self, station, run_id, words, *, volume="east", settings=SETTINGS):
        frame = self.frame(run_id, words, settings)
        receipt = archive_frame(station, frame, volume)
        release_frame(self.slots, station, receipt.receipt_id)
        return frame, receipt

"""Unit tests for ranger-sync core logic."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from ranger_sync.anomaly import (
    PhysicalOverlapError,
    apply_shift_spec,
    assert_physical_continuity,
    auto_detect_clock_skew,
    detect_timeline_overlaps,
    parse_offset_seconds,
)
from ranger_sync.gpx import GpxIndex, haversine_m
from ranger_sync.models import MediaItem, MediaKind, TrackPoint
from ranger_sync.parser import classify_filename
from pathlib import Path


class TestRangerSync(unittest.TestCase):
    def test_classify_filename(self) -> None:
        self.assertEqual(
            classify_filename("IMG_1793437400537.JPG"),
            (MediaKind.BLEEQUP_PHOTO, 1793437400537),
        )
        self.assertEqual(
            classify_filename("VIDEO_1793434415483.MP4"),
            (MediaKind.BLEEQUP_VIDEO, 1793434415483),
        )
        self.assertEqual(
            classify_filename("BleeqUp_Motion_1793450679296.PNG"),
            (MediaKind.BLEEQUP_AI_MOTION, 1793450679296),
        )
        self.assertEqual(
            classify_filename("VIDEO_HIGHLIGHT_1793449785293.MP4"),
            (MediaKind.BLEEQUP_AI_VIDEO, 1793449785293),
        )
        self.assertEqual(
            classify_filename("BleeqUp_Remix_1793450123748_ai.MP4"),
            (MediaKind.BLEEQUP_AI_VIDEO, 1793450123748),
        )
        self.assertEqual(
            classify_filename("IMG_8591.MOV"),
            (MediaKind.REFERENCE_IPHONE, None),
        )
        self.assertIsNone(classify_filename("Context.md"))

    def test_parse_offset_seconds(self) -> None:
        self.assertEqual(parse_offset_seconds("-3837s"), -3837.0)
        self.assertEqual(parse_offset_seconds("-1h"), -3600.0)
        self.assertEqual(parse_offset_seconds("-1h3m57s"), -3837.0)
        self.assertEqual(parse_offset_seconds("+3600"), 3600.0)

    def test_stationary_stop_interpolation(self) -> None:
        # 33-minute cafe pause with <50m movement
        p1 = TrackPoint(timestamp=1000.0, lat=39.89735, lon=3.07572, ele=10.0, source_file="ride.gpx")
        p2 = TrackPoint(timestamp=3000.0, lat=39.89705, lon=3.07535, ele=12.0, source_file="ride.gpx")
        idx = GpxIndex([p1, p2])
        fix = idx.interpolate(target_ts=2000.0, max_moving_gap_sec=60.0, stationary_radius_m=150.0)
        self.assertIsNotNone(fix)
        assert fix is not None
        self.assertEqual(fix.mode, "stationary_stop")
        self.assertAlmostEqual(fix.lat, 39.89720, places=5)
        self.assertAlmostEqual(fix.ele or 0.0, 11.0, places=2)

    def test_overlap_and_shift_rename(self) -> None:
        t0 = 1793437571.094
        vid_long = MediaItem(
            path=Path("/tmp/VIDEO_1793437571094.MP4"),
            filename="VIDEO_1793437571094.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1793437571094,
            raw_utc=datetime.fromtimestamp(t0, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(t0, tz=timezone.utc),
            duration_sec=180.0,
        )
        vid_overlap = MediaItem(
            path=Path("/tmp/VIDEO_1793437646586.MP4"),
            filename="VIDEO_1793437646586.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1793437646586,
            raw_utc=datetime.fromtimestamp(1793437646.586, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1793437646.586, tz=timezone.utc),
            duration_sec=60.0,
        )
        items = [vid_long, vid_overlap]
        conflicts = detect_timeline_overlaps(items)
        self.assertEqual(len(conflicts), 1)

        apply_shift_spec(items, "VIDEO_1793437646586.MP4:-3837s")
        conflicts_after = detect_timeline_overlaps(items)
        self.assertEqual(len(conflicts_after), 0)
        self.assertEqual(
            vid_overlap.new_filename_for_shift(),
            "VIDEO_1793433809586.MP4",
        )

    def test_auto_detect_clock_skew(self) -> None:
        # Simulate Oct 31 mid-ride Camera Settings handshake overlap
        gpx = GpxIndex(
            [
                TrackPoint(timestamp=1793432650.0, lat=39.86, lon=3.14, ele=10.0),
                TrackPoint(timestamp=1793433965.0, lat=39.8972, lon=3.0754, ele=3.0),
                TrackPoint(timestamp=1793435939.0, lat=39.8972, lon=3.0754, ele=3.0),
                TrackPoint(timestamp=1793442739.0, lat=39.86, lon=3.14, ele=10.0),
            ]
        )
        # Pre-sync items (+3770s fast) mixed with a 180s post-sync mid-ride item
        pre1 = MediaItem(
            path=Path("/tmp/VIDEO_1793437646586.MP4"),
            filename="VIDEO_1793437646586.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1793437646586,
            raw_utc=datetime.fromtimestamp(1793437646.586, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1793437646.586, tz=timezone.utc),
            duration_sec=60.0,
        )
        pre2 = MediaItem(
            path=Path("/tmp/VIDEO_1793437709396.MP4"),
            filename="VIDEO_1793437709396.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1793437709396,
            raw_utc=datetime.fromtimestamp(1793437709.396, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1793437709.396, tz=timezone.utc),
            duration_sec=60.0,
        )
        mid_peloton = MediaItem(
            path=Path("/tmp/VIDEO_1793437571094.MP4"),
            filename="VIDEO_1793437571094.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1793437571094,
            raw_utc=datetime.fromtimestamp(1793437571.094, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1793437571.094, tz=timezone.utc),
            duration_sec=180.0,
        )
        items = [pre1, pre2, mid_peloton]
        shifted, desc = auto_detect_clock_skew(items, gpx)
        self.assertEqual(len(shifted), 2)
        self.assertIn(pre1, shifted)
        self.assertIn(pre2, shifted)
        self.assertNotIn(mid_peloton, shifted)
        self.assertEqual(len(detect_timeline_overlaps(items)), 0)

    def test_subsecond_batch_remux_overlap_aborts_autosync(self) -> None:
        # Simulate Nov 10 Port d'Alcúdia 91ms reverse batch-remux collision
        gpx = GpxIndex(
            [
                TrackPoint(timestamp=1794302511.0, lat=39.86, lon=3.14, ele=10.0),
                TrackPoint(timestamp=1794312443.0, lat=39.86, lon=3.14, ele=10.0),
            ]
        )
        c1 = MediaItem(
            path=Path("/tmp/VIDEO_1794306133733.MP4"),
            filename="VIDEO_1794306133733.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1794306133733,
            raw_utc=datetime.fromtimestamp(1794306133.733, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1794306133.733, tz=timezone.utc),
            duration_sec=55.3,
        )
        c2 = MediaItem(
            path=Path("/tmp/VIDEO_1794306133824.MP4"),
            filename="VIDEO_1794306133824.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1794306133824,
            raw_utc=datetime.fromtimestamp(1794306133.824, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1794306133.824, tz=timezone.utc),
            duration_sec=75.7,
        )
        with self.assertRaisesRegex(
            PhysicalOverlapError,
            r"CRITICAL: Non-physical BleeqUP timestamp overlap detected",
        ):
            auto_detect_clock_skew([c1, c2], gpx)
        with self.assertRaisesRegex(
            PhysicalOverlapError,
            r"CRITICAL: Non-physical BleeqUP timestamp overlap detected",
        ):
            assert_physical_continuity([c1, c2])

    def test_epoch_1_8b_2027_rollover_and_photo_batch_and_brownout(self) -> None:
        # 1. Jan 2027 Epoch 1.8B rollover
        self.assertEqual(
            classify_filename("IMG_1800000000123.JPG"),
            (MediaKind.BLEEQUP_PHOTO, 1800000000123),
        )

        # 2. <250ms still photo batch-export collision
        p1 = MediaItem(
            path=Path("IMG_1762770000000.JPG"),
            filename="IMG_1762770000000.JPG",
            kind=MediaKind.BLEEQUP_PHOTO,
            raw_epoch_ms=1762770000000,
            raw_utc=datetime.fromtimestamp(1762770000.0, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1762770000.0, tz=timezone.utc),
            duration_sec=0.0,
        )
        p2 = MediaItem(
            path=Path("IMG_1762770000091.JPG"),
            filename="IMG_1762770000091.JPG",
            kind=MediaKind.BLEEQUP_PHOTO,
            raw_epoch_ms=1762770000091,
            raw_utc=datetime.fromtimestamp(1762770000.091, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1762770000.091, tz=timezone.utc),
            duration_sec=0.0,
        )
        with self.assertRaises(PhysicalOverlapError):
            assert_physical_continuity([p1, p2], use_raw=True)

        # 3. Backward -55m RTC brownout landing inside a silent cafe pause
        v_early = MediaItem(
            path=Path("VIDEO_1762765200000.MP4"),
            filename="VIDEO_1762765200000.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1762765200000,
            raw_utc=datetime.fromtimestamp(1762765200.0, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1762765200.0, tz=timezone.utc),
            duration_sec=20.0,
            internal_create_utc=datetime.fromtimestamp(1762765200.0, tz=timezone.utc),
        )
        v_brownout = MediaItem(
            path=Path("VIDEO_1762768200000.MP4"),
            filename="VIDEO_1762768200000.MP4",
            kind=MediaKind.BLEEQUP_VIDEO,
            raw_epoch_ms=1762768200000,
            raw_utc=datetime.fromtimestamp(1762768200.0, tz=timezone.utc),
            corrected_utc=datetime.fromtimestamp(1762768200.0, tz=timezone.utc),
            duration_sec=20.0,
            internal_create_utc=datetime.fromtimestamp(1762771500.0, tz=timezone.utc),
        )
        with self.assertRaisesRegex(PhysicalOverlapError, r"brownout|monotonic"):
            assert_physical_continuity([v_early, v_brownout], use_raw=True)


if __name__ == "__main__":
    unittest.main()

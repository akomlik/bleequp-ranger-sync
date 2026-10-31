"""Timeline overlap conflict detection, clock-skew correction, and dual-camera matching."""

from __future__ import annotations

import fnmatch
import re
from datetime import timedelta

from .gpx import haversine_m
from .models import MediaItem, MediaKind


def parse_offset_seconds(offset_str: str) -> float:
    """Parse an offset string such as '-3837s', '-3600', '-1h', '-1h3m57s' into seconds."""
    s = offset_str.strip()
    if re.match(r"^[+-]?\d+(?:\.\d+)?s?$", s, re.IGNORECASE):
        return float(s.rstrip("sS"))

    m = re.match(
        r"^([+-]?)(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?$",
        s,
        re.IGNORECASE,
    )
    if m and any(m.groups()[1:]):
        sign = -1.0 if m.group(1) == "-" else 1.0
        hours = float(m.group(2) or 0)
        minutes = float(m.group(3) or 0)
        seconds = float(m.group(4) or 0)
        return sign * (hours * 3600.0 + minutes * 60.0 + seconds)

    raise ValueError(f"Invalid time offset specification: {offset_str!r}")


def apply_shift_spec(items: list[MediaItem], shift_spec: str | None) -> list[MediaItem]:
    """Apply `--shift-files` specification (e.g. 'FILE1,FILE2:-3837s') to matching items."""
    if not shift_spec:
        return []

    shifted: list[MediaItem] = []
    # Allow multiple rules separated by ';'
    for rule in shift_spec.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        if ":" not in rule:
            raise ValueError(
                f"Shift rule {rule!r} must be in format 'FILE1,FILE2:<offset>' (e.g. 'IMG_1*.jpg:-3600s')"
            )
        patterns_part, offset_part = rule.rsplit(":", 1)
        offset_sec = parse_offset_seconds(offset_part)
        patterns = [p.strip() for p in patterns_part.split(",") if p.strip()]

        for item in items:
            if not item.is_bleequp:
                continue
            if any(fnmatch.fnmatch(item.filename, pat) for pat in patterns):
                item.time_shift_sec = offset_sec
                item.corrected_utc = item.raw_utc + timedelta(seconds=offset_sec)
                item.shift_reason = f"manual_shift({offset_part.strip()})"
                shifted.append(item)

    return shifted


def detect_timeline_overlaps(items: list[MediaItem]) -> list[tuple[MediaItem, MediaItem]]:
    """Detect physically impossible overlaps among BleeqUP raw captures (single camera)."""
    for item in items:
        item.overlap_conflicts.clear()

    raw_captures = sorted(
        [
            i
            for i in items
            if i.kind in (MediaKind.BLEEQUP_PHOTO, MediaKind.BLEEQUP_VIDEO)
        ],
        key=lambda x: x.corrected_utc,
    )

    conflicts: list[tuple[MediaItem, MediaItem]] = []
    for idx, a in enumerate(raw_captures):
        if a.duration_sec <= 1.0:
            continue
        a_start = a.corrected_utc.timestamp()
        a_end = a_start + a.duration_sec
        for b in raw_captures[idx + 1 :]:
            b_start = b.corrected_utc.timestamp()
            # Allow 1.5s tolerance at boundary of back-to-back clips
            if b_start < a_end - 1.5:
                a.overlap_conflicts.append(b.filename)
                b.overlap_conflicts.append(a.filename)
                conflicts.append((a, b))
            else:
                break

    return conflicts


def detect_simultaneous_shots(
    items: list[MediaItem],
    tolerance_sec: float = 15.0,
) -> list[tuple[MediaItem, MediaItem, float, float | None]]:
    """Find BleeqUP captures that overlap or occur within tolerance_sec of iPhone captures.

    Returns list of (bleequp_item, iphone_item, time_diff_sec, gps_diff_m).
    """
    for item in items:
        item.simultaneous_refs.clear()

    bleequp_items = [
        i
        for i in items
        if i.kind in (MediaKind.BLEEQUP_PHOTO, MediaKind.BLEEQUP_VIDEO)
    ]
    iphone_items = [i for i in items if i.kind == MediaKind.REFERENCE_IPHONE]

    pairs: list[tuple[MediaItem, MediaItem, float, float | None]] = []
    for b in bleequp_items:
        b_start = b.corrected_utc.timestamp()
        b_end = b_start + max(0.0, b.duration_sec)
        for ref in iphone_items:
            r_start = ref.corrected_utc.timestamp()
            r_end = r_start + max(0.0, ref.duration_sec)
            # Distance between intervals [b_start, b_end] and [r_start, r_end]
            if b_end < r_start:
                gap = r_start - b_end
            elif r_end < b_start:
                gap = b_start - r_end
            else:
                gap = 0.0

            if gap <= tolerance_sec:
                start_diff = b_start - r_start
                gps_diff: float | None = None
                if (
                    b.gpx_fix is not None
                    and ref.existing_lat is not None
                    and ref.existing_lon is not None
                ):
                    gps_diff = haversine_m(
                        b.gpx_fix.lat,
                        b.gpx_fix.lon,
                        ref.existing_lat,
                        ref.existing_lon,
                    )
                b.simultaneous_refs.append(ref.filename)
                ref.simultaneous_refs.append(b.filename)
                pairs.append((b, ref, start_diff, gps_diff))

    return pairs

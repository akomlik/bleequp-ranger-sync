"""Timeline overlap conflict detection, clock-skew correction, and dual-camera matching."""

from __future__ import annotations

import fnmatch
import re
from datetime import timedelta

from .gpx import haversine_m
from .models import MediaItem, MediaKind


class PhysicalOverlapError(RuntimeError):
    """Raised when non-physical BleeqUP timestamp overlaps prevent safe auto-sync or metadata writes."""


def check_physical_overlaps(
    items: list[MediaItem],
    *,
    use_raw: bool = False,
    boundary_tolerance_sec: float = 1.0,
) -> list[tuple[MediaItem, MediaItem, float]]:
    """Sort BleeqUP clips by timestamp and check `ts[i+1] - ts[i] < clip_duration[i]`.

    Returns list of `(clip_i, clip_next, delta_sec)` violations.
    """
    ts_fn = (lambda x: x.raw_utc.timestamp()) if use_raw else (lambda x: x.corrected_utc.timestamp())
    raw_captures = sorted(
        [
            i
            for i in items
            if i.kind in (MediaKind.BLEEQUP_PHOTO, MediaKind.BLEEQUP_VIDEO)
        ],
        key=ts_fn,
    )
    violations: list[tuple[MediaItem, MediaItem, float]] = []
    for idx in range(len(raw_captures) - 1):
        curr = raw_captures[idx]
        nxt = raw_captures[idx + 1]
        delta_s = ts_fn(nxt) - ts_fn(curr)
        # Sub-second collision (e.g. 91ms app batch-remux) or overlap inside clip duration
        effective_dur = max(0.0, curr.duration_sec - boundary_tolerance_sec) if curr.duration_sec > 1.0 else 0.0
        if (curr.duration_sec > 1.0 and delta_s < effective_dur) or (curr.duration_sec > 0.0 and delta_s < 0.25):
            violations.append((curr, nxt, delta_s))
    return violations


def assert_physical_continuity(items: list[MediaItem], *, use_raw: bool = False) -> None:
    """Abort immediately if any non-physical BleeqUP timestamp overlap remains."""
    violations = check_physical_overlaps(items, use_raw=use_raw)
    if violations:
        details = "; ".join(
            f"{a.filename} (dur {a.duration_sec:.1f}s) -> {b.filename} (Δt={dt_s*1000:.0f}ms)"
            for a, b, dt_s in violations[:5]
        )
        raise PhysicalOverlapError(
            f"CRITICAL: Non-physical BleeqUP timestamp overlap detected "
            f"(likely RTC brownout or app batch-remux): {details}"
        )


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


def auto_detect_clock_skew(
    items: list[MediaItem],
    gpx_index,
) -> tuple[list[MediaItem], str | None]:
    """Automatically detect and resolve BleeqUP Camera SoC sleep-oscillator clock skew.

    Handles two physical patterns:
      1. Whole-ride unsynced drift (e.g. Oct 29 Coll de sa Batalla `-2482s`):
         All BleeqUP captures are shifted forward by +Δt, causing trailing clips to fall
         past `gpx_index.end_utc`.
      2. Mid-ride Camera Settings handshake (e.g. Oct 31 Can Picafort `-3770s`):
         Early pre-sync captures carry +Δt sleep-oscillator drift and land on top of
         mid-ride post-sync captures, creating physical `overlap_conflicts` while leaving
         the pre-stop GPX riding segment empty.
    """
    raw_captures = sorted(
        [
            i
            for i in items
            if i.kind in (MediaKind.BLEEQUP_PHOTO, MediaKind.BLEEQUP_VIDEO)
        ],
        key=lambda x: x.raw_utc,
    )
    if not raw_captures or gpx_index is None or gpx_index.is_empty:
        return [], None

    # Do not auto-shift if manual or existing EXIF shifts are already present
    if any(abs(i.time_shift_sec) >= 2.0 for i in raw_captures):
        return [], None

    # Pre-flight Physical Overlap Assertion for sub-second app batch-remux collisions
    # (e.g. clips stamped 91ms apart while several seconds long — no uniform shift can separate them)
    raw_violations = check_physical_overlaps(raw_captures, use_raw=True)
    subsec_batch = [(a, b, dt_s) for a, b, dt_s in raw_violations if dt_s < 1.0 and a.duration_sec > 2.0]
    if subsec_batch:
        details = "; ".join(
            f"{a.filename} (dur {a.duration_sec:.1f}s) -> {b.filename} (Δt={dt_s*1000:.0f}ms)"
            for a, b, dt_s in subsec_batch[:5]
        )
        raise PhysicalOverlapError(
            f"CRITICAL: Non-physical BleeqUP timestamp overlap detected "
            f"(likely RTC brownout or app batch-remux): {details}"
        )

    gpx_start = gpx_index.timestamps[0]
    gpx_end = gpx_index.timestamps[-1]

    # Pattern 1: Whole-ride unsynced sleep-oscillator drift (trailing clips past GPX end)
    overlaps = detect_timeline_overlaps(items)
    last_end = max(i.raw_utc.timestamp() + max(0.0, i.duration_sec) for i in raw_captures)
    first_start = min(i.raw_utc.timestamp() for i in raw_captures)
    if not overlaps and (gpx_end + 180.0 < last_end <= gpx_end + 14400.0):
        # Verify shifting all clips back keeps first_start near or after gpx_start - 300s
        offset_sec = float(round(gpx_end - last_end))
        if first_start + offset_sec >= gpx_start - 300.0:
            shifted: list[MediaItem] = []
            for item in raw_captures:
                item.time_shift_sec = offset_sec
                item.corrected_utc = item.raw_utc + timedelta(seconds=offset_sec)
                item.shift_reason = f"auto_whole_ride_drift({offset_sec:+.0f}s)"
                shifted.append(item)
            return shifted, f"*:{offset_sec:+.0f}s (whole-ride Camera SoC drift)"

    # Pattern 2: Mid-ride Camera Settings handshake (physical overlap conflicts)
    if overlaps:
        pauses = [
            (pa, pb, gap_s, dist_m)
            for pa, pb, gap_s, dist_m in gpx_index.detect_pauses(min_gap_sec=180.0)
            if dist_m <= 150.0
        ]
        if pauses:
            pa, pb, _, _ = pauses[0]
            stop_start = pa.timestamp
            stop_end = pb.timestamp
            # Identify the window of items around the overlap conflict
            conflict_names = {a.filename for a, _ in overlaps} | {b.filename for _, b in overlaps}
            conflict_times = [i.raw_utc.timestamp() for i in raw_captures if i.filename in conflict_names]
            win_min = min(conflict_times) - 600.0
            win_max = max(conflict_times) + 600.0
            window_items = [
                i for i in raw_captures if win_min <= i.raw_utc.timestamp() <= win_max
            ]

            best_subset: list[MediaItem] = []
            best_offset: float = 0.0
            # Try each item in window_items as the stop-arrival clip (where clip start + ~25s == stop_start)
            for anchor in window_items:
                anchor_stop_ts = anchor.raw_utc.timestamp() + min(25.0, max(0.0, anchor.duration_sec * 0.45))
                cand_offset = float(round(stop_start - anchor_stop_ts))
                if not (-14400.0 <= cand_offset <= -180.0):
                    continue
                # Select maximal non-overlapping subset of window_items that lands inside [gpx_start - 180, stop_end]
                cand_subset: list[MediaItem] = []
                for w in window_items:
                    shifted_t = w.raw_utc.timestamp() + cand_offset
                    if not (gpx_start - 180.0 <= shifted_t <= stop_end - 60.0):
                        continue
                    # Check if w overlaps with any longer item in window_items that is NOT shifted
                    # Wait: the real mid-ride clip is the one that collides with multiple shorter pre-sync items
                    cand_subset.append(w)

                # Filter out any single item inside cand_subset that causes internal overlaps within cand_subset
                filtered_subset: list[MediaItem] = []
                for idx, w in enumerate(cand_subset):
                    w_s = w.raw_utc.timestamp()
                    w_e = w_s + max(0.0, w.duration_sec)
                    internal_collisions = sum(
                        1
                        for other in cand_subset
                        if other is not w
                        and not (other.raw_utc.timestamp() + max(0.0, other.duration_sec) - 1.5 <= w_s or other.raw_utc.timestamp() >= w_e - 1.5)
                    )
                    if internal_collisions >= 2:
                        # This item (e.g. a 180s mid-ride peloton clip) collides with >=2 items in the pre-sync sequence; leave it unshifted!
                        continue
                    filtered_subset.append(w)

                # Check if shifting filtered_subset by cand_offset eliminates all overlaps
                for i in raw_captures:
                    if i in filtered_subset:
                        i.corrected_utc = i.raw_utc + timedelta(seconds=cand_offset)
                    else:
                        i.corrected_utc = i.raw_utc
                rem_overlaps = detect_timeline_overlaps(items)
                if len(rem_overlaps) == 0 and len(filtered_subset) > len(best_subset):
                    best_subset = list(filtered_subset)
                    best_offset = cand_offset

            # Restore before applying best
            for i in raw_captures:
                i.corrected_utc = i.raw_utc
            detect_timeline_overlaps(items)

            if best_subset and best_offset != 0.0:
                for item in best_subset:
                    item.time_shift_sec = best_offset
                    item.corrected_utc = item.raw_utc + timedelta(seconds=best_offset)
                    item.shift_reason = f"auto_midride_sync({best_offset:+.0f}s)"
                detect_timeline_overlaps(items)
                names_spec = ",".join(i.filename for i in best_subset)
                return best_subset, f"{names_spec}:{best_offset:+.0f}s (pre-sync Camera SoC cluster)"

        # If physical overlaps existed and auto-sync could not cleanly resolve them (e.g. multi-step RTC brownout), abort!
        assert_physical_continuity(items)

    return [], None

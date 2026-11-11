#!/usr/bin/env python3
"""CLI entry point for ranger-sync (BleeqUP Ranger Media & GPX Sync Utility)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from .anomaly import (
    PhysicalOverlapError,
    apply_shift_spec,
    assert_physical_continuity,
    auto_detect_clock_skew,
    detect_simultaneous_shots,
    detect_timeline_overlaps,
)
from .gpx import GpxIndex
from .injector import apply_metadata_and_renames
from .models import MediaItem, MediaKind
from .parser import scan_media_directory
from .vision_match import match_ai_motion_images


def add_common_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "directory",
        type=Path,
        help="Path to ride directory containing media and GPX files",
    )
    p.add_argument(
        "--gpx",
        type=Path,
        help="Optional explicit GPX file or directory (defaults to *.gpx in ride directory)",
    )
    p.add_argument(
        "--timezone",
        default="Europe/Madrid",
        help="IANA timezone name for local EXIF/Keys timestamps (default: Europe/Madrid)",
    )
    p.add_argument(
        "--shift-files",
        help="Clock-skew correction spec, e.g. 'FILE1,FILE2:-3837s' (supports globs & ';' separated rules)",
    )
    p.add_argument(
        "--max-gap",
        type=float,
        default=120.0,
        help="Max moving time gap in seconds to nearest GPX trackpoint (default: 120)",
    )
    p.add_argument(
        "--stationary-radius",
        type=float,
        default=150.0,
        help="Max distance in meters between GPX points to treat a pause as stationary (default: 150)",
    )
    p.add_argument(
        "--max-stationary-gap",
        type=float,
        default=7200.0,
        help="Max duration in seconds for a stationary GPX pause (default: 7200 = 2h)",
    )
    p.add_argument(
        "--boundary-tolerance",
        type=float,
        default=300.0,
        help="Tolerance in seconds before/after GPX start/end to clamp position (default: 300)",
    )
    p.add_argument(
        "--no-vision-match",
        action="store_true",
        help="Disable macOS Vision perceptual matching of BleeqUp_Motion_* AI images to source IMG_*",
    )
    p.add_argument(
        "--sync-ai-motion-time",
        action="store_true",
        default=False,
        help="Shift BleeqUp_Motion_* timestamp to matched source photo (+1s) instead of keeping generation time",
    )
    p.add_argument(
        "--auto-shift",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Automatically detect and correct BleeqUP Camera SoC sleep-oscillator clock skew against GPX (default: true)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ranger-sync",
        description="Sync timestamps, fix RTC/DST clock jumps, and geotag BleeqUP Ranger media from GPX tracks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_p = subparsers.add_parser(
        "inspect",
        help="Scan a ride directory, align with GPX & reference iPhone media, and report overlaps/anomalies.",
    )
    add_common_arguments(inspect_p)

    apply_p = subparsers.add_parser(
        "apply",
        help="Inject EXIF/QuickTime timestamps, GPS coordinates, and update macOS filesystem dates.",
    )
    add_common_arguments(apply_p)
    apply_p.add_argument(
        "--write-filesystem",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Update macOS Finder creation (btime) and modification (mtime) dates (default: true)",
    )
    apply_p.add_argument(
        "--overwrite-original",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Overwrite files in-place without leaving *_original backups (default: true)",
    )
    apply_p.add_argument(
        "--rename-shifted",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rename time-shifted BleeqUP files so their 13-digit filename epoch matches corrected time (default: true)",
    )
    apply_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview metadata writes and renames without modifying files on disk",
    )
    apply_p.add_argument(
        "--allow-overlaps",
        action="store_true",
        default=False,
        help="Emergency override: allow writing metadata even when non-physical BleeqUP overlaps remain",
    )

    return parser


def prepare_pipeline(args: argparse.Namespace) -> tuple[ZoneInfo, GpxIndex, list[MediaItem]]:
    directory: Path = args.directory.expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    tz = ZoneInfo(args.timezone)
    gpx_paths = [args.gpx.expanduser().resolve()] if args.gpx else [directory]
    gpx_index = GpxIndex.from_paths(gpx_paths)

    items = scan_media_directory(directory)
    if args.shift_files:
        apply_shift_spec(items, args.shift_files)
    elif getattr(args, "auto_shift", True):
        try:
            auto_shifted, auto_desc = auto_detect_clock_skew(items, gpx_index)
            if auto_shifted and auto_desc:
                print(f"[✓] AUTO-SOLVED BLEEQUP CLOCK SKEW ({len(auto_shifted)} files): {auto_desc}")
        except PhysicalOverlapError as exc:
            if args.command == "inspect" or getattr(args, "allow_overlaps", False):
                print(f"[!] {exc}", file=sys.stderr)
            else:
                raise

    # Interpolate GPX fixes for raw BleeqUP captures first
    for item in items:
        if item.kind in (MediaKind.BLEEQUP_PHOTO, MediaKind.BLEEQUP_VIDEO):
            item.gpx_fix = gpx_index.interpolate(
                target_ts=item.corrected_utc.timestamp(),
                max_moving_gap_sec=args.max_gap,
                max_stationary_gap_sec=args.max_stationary_gap,
                stationary_radius_m=args.stationary_radius,
                boundary_tolerance_sec=args.boundary_tolerance,
            )

    # Match BleeqUp_Motion_* AI images to source IMG_* photos (inherits GPS fix, keeps AI gen time unless requested)
    if not args.no_vision_match:
        match_ai_motion_images(
            items=items,
            sync_time_to_source=args.sync_ai_motion_time,
        )

    # For any unmatched AI files, try standard GPX interpolation (usually None if generated post-ride)
    for item in items:
        if item.is_ai_generated and item.gpx_fix is None:
            item.gpx_fix = gpx_index.interpolate(
                target_ts=item.corrected_utc.timestamp(),
                max_moving_gap_sec=args.max_gap,
                max_stationary_gap_sec=args.max_stationary_gap,
                stationary_radius_m=args.stationary_radius,
                boundary_tolerance_sec=args.boundary_tolerance,
            )

    detect_timeline_overlaps(items)
    detect_simultaneous_shots(items)
    return tz, gpx_index, items


def print_report(
    directory: Path,
    tz: ZoneInfo,
    gpx_index: GpxIndex,
    items: list[MediaItem],
    rename_shifted: bool = False,
) -> None:
    bleequp_items = [i for i in items if i.is_bleequp]
    ref_items = [i for i in items if i.kind == MediaKind.REFERENCE_IPHONE]

    print(f"=== BleeqUP Ranger Sync Report: {directory.name} ===")
    print(f"Timezone        : {tz.key}")
    print(
        f"Media Discovered: {len(bleequp_items)} BleeqUP files "
        f"({sum(1 for i in bleequp_items if not i.is_ai_generated)} raw, "
        f"{sum(1 for i in bleequp_items if i.is_ai_generated)} AI-generated), "
        f"{len(ref_items)} iPhone reference files"
    )

    if not gpx_index.is_empty and gpx_index.start_utc and gpx_index.end_utc:
        s_loc = gpx_index.start_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        e_loc = gpx_index.end_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        print(f"GPX Track Range : {s_loc} -> {e_loc} ({len(gpx_index.points)} trackpoints)")
        pauses = gpx_index.detect_pauses(min_gap_sec=180.0)
        for pa, pb, gap_s, dist_m in pauses:
            pa_s = gpx_index.start_utc.fromtimestamp(pa.timestamp, tz=tz).strftime("%H:%M:%S")
            pb_s = gpx_index.start_utc.fromtimestamp(pb.timestamp, tz=tz).strftime("%H:%M:%S")
            kind_str = "STATIONARY STOP" if dist_m <= 150.0 else "TRACK GAP"
            print(
                f"  • {kind_str}: {pa_s} -> {pb_s} ({gap_s/60.0:.1f} min, displacement {dist_m:.1f} m)"
            )
    else:
        print("GPX Track Range : No GPX trackpoints found")

    # Report overlaps
    overlaps = [i for i in bleequp_items if i.overlap_conflicts]
    if overlaps:
        print(f"\n[!] WARNING: {len(overlaps)} BleeqUP files have physical timeline overlaps (possible RTC/DST clock jump):")
        for item in overlaps:
            loc_t = item.corrected_utc.astimezone(tz).strftime("%H:%M:%S")
            print(
                f"  • {item.filename} ({loc_t}, dur {item.duration_sec:.1f}s) overlaps with: {', '.join(item.overlap_conflicts)}"
            )
    else:
        print("\n[✓] Timeline Continuity: 0 physical overlap conflicts among BleeqUP captures.")

    # Report AI Motion visual matches
    matched_ai = [i for i in bleequp_items if i.matched_source_file]
    if matched_ai:
        print(f"\n[✓] AI Motion Perceptual Matches (macOS Vision):")
        for item in matched_ai:
            gps_s = (
                f"({item.gpx_fix.lat:.5f}, {item.gpx_fix.lon:.5f})"
                if item.gpx_fix
                else "no GPS"
            )
            print(
                f"  • {item.filename:36s} -> {item.matched_source_file} "
                f"(feature dist: {item.matched_distance:.3f}, inherited GPS: {gps_s})"
            )

    # Report simultaneous dual-camera shots
    sim_pairs = detect_simultaneous_shots(items)
    if sim_pairs:
        print(f"\n[✓] Simultaneous Dual-Camera Captures (BleeqUP + iPhone within ±15s):")
        for b, ref, dt_s, gps_diff_m in sim_pairs:
            b_t = b.corrected_utc.astimezone(tz).strftime("%H:%M:%S")
            r_t = ref.corrected_utc.astimezone(tz).strftime("%H:%M:%S")
            dist_str = f"GPS delta: {gps_diff_m:.1f} m" if gps_diff_m is not None else "GPS delta: N/A"
            print(
                f"  • {b.filename:28s} ({b_t}) <-> {ref.filename:14s} ({r_t}) | "
                f"Δt={dt_s:+.1f}s | {dist_str}"
            )

    # Unified Chronological Timeline Table
    print("\n--- Unified Chronological Timeline (Local Time) ---")
    header = (
        f"{'Local Time':19s} | {'Source':9s} | {'Filename':36s} | "
        f"{'Dur':6s} | {'Shift':8s} | {'GPS Coordinates / Fix Mode'}"
    )
    print(header)
    print("-" * len(header))

    for item in sorted(items, key=lambda x: x.corrected_utc):
        loc_dt = item.corrected_utc.astimezone(tz)
        t_str = loc_dt.strftime("%Y-%m-%d %H:%M:%S")
        src = "iPhone" if item.kind == MediaKind.REFERENCE_IPHONE else (
            "Bleeq-AI" if item.is_ai_generated else "BleeqUP"
        )
        dur_str = f"{item.duration_sec:5.1f}s" if item.duration_sec > 0 else "     -"
        shift_str = f"{item.time_shift_sec:+.0f}s" if item.time_shift_sec != 0 else "       -"

        if item.kind == MediaKind.REFERENCE_IPHONE:
            if item.existing_lat is not None and item.existing_lon is not None:
                gps_info = f"{item.existing_lat:.5f}, {item.existing_lon:.5f} [iPhone EXIF]"
            else:
                gps_info = "- [iPhone no GPS]"
        else:
            if item.gpx_fix is not None:
                ele_s = f", {item.gpx_fix.ele:.1f}m" if item.gpx_fix.ele is not None else ""
                gps_info = f"{item.gpx_fix.lat:.5f}, {item.gpx_fix.lon:.5f}{ele_s} [{item.gpx_fix.mode}]"
            else:
                gps_info = "OUT OF TRACK RANGE (timestamps only)"

        fname_display = item.filename
        if rename_shifted and item.time_shift_sec != 0.0:
            fname_display = f"{item.filename} -> {item.new_filename_for_shift()}"

        conflict_marker = " [!OVERLAP]" if item.overlap_conflicts else ""
        print(
            f"{t_str:19s} | {src:9s} | {fname_display:36s} | "
            f"{dur_str:6s} | {shift_str:8s} | {gps_info}{conflict_marker}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        tz, gpx_index, items = prepare_pipeline(args)
    except PhysicalOverlapError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    rename_flag = getattr(args, "rename_shifted", False)
    print_report(
        directory=args.directory.expanduser().resolve(),
        tz=tz,
        gpx_index=gpx_index,
        items=items,
        rename_shifted=rename_flag,
    )

    if args.command == "inspect":
        return 0

    if args.command == "apply":
        if not getattr(args, "allow_overlaps", False):
            try:
                assert_physical_continuity(items)
            except PhysicalOverlapError as exc:
                print(f"\n{exc}", file=sys.stderr)
                return 2
        dry_run = args.dry_run
        mode_label = "DRY RUN (no files modified)" if dry_run else "APPLYING METADATA"
        print(f"\n=== {mode_label} ===")
        count, renames = apply_metadata_and_renames(
            items=items,
            tz=tz,
            write_filesystem=args.write_filesystem,
            overwrite_original=args.overwrite_original,
            rename_shifted=args.rename_shifted,
            dry_run=dry_run,
        )
        action_verb = "Would update" if dry_run else "Updated"
        print(f"[✓] {action_verb} metadata on {count} BleeqUP media files.")
        if renames:
            ren_verb = "Would rename" if dry_run else "Renamed"
            print(f"[✓] {ren_verb} {len(renames)} time-shifted files:")
            for old_n, new_n in renames:
                print(f"  • {old_n} -> {new_n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

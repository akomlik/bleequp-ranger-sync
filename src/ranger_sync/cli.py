#!/usr/bin/env python3
"""CLI entry point for ranger-sync (BleeqUP Ranger Media & GPX Sync Utility)."""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ranger-sync",
        description="Sync timestamps, fix RTC/DST clock jumps, and geotag BleeqUP Ranger media from GPX tracks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # inspect command
    inspect_p = subparsers.add_parser(
        "inspect",
        help="Scan a directory, align with GPX & reference iPhone media, and report overlaps/anomalies.",
    )
    inspect_p.add_argument("directory", help="Path to ride directory containing media and GPX files")
    inspect_p.add_argument("--timezone", default="Europe/Madrid", help="IANA timezone name (default: Europe/Madrid)")

    # apply command
    apply_p = subparsers.add_parser(
        "apply",
        help="Inject EXIF/QuickTime timestamps, GPS coordinates, and update macOS filesystem dates.",
    )
    apply_p.add_argument("directory", help="Path to ride directory containing media and GPX files")
    apply_p.add_argument("--gpx", help="Optional explicit GPX file or directory (defaults to *.gpx in directory)")
    apply_p.add_argument("--timezone", default="Europe/Madrid", help="IANA timezone name (default: Europe/Madrid)")
    apply_p.add_argument(
        "--shift-files",
        help="Clock-skew correction spec, e.g. 'FILE1,FILE2:-3837s' or 'auto'",
    )
    apply_p.add_argument(
        "--match-ai-motion",
        action="store_true",
        default=True,
        help="Match BleeqUp_Motion_* AI images to their source IMG_* photo via macOS Vision feature prints",
    )
    apply_p.add_argument(
        "--write-filesystem",
        action="store_true",
        default=True,
        help="Update macOS Finder creation (btime) and modification (mtime) dates",
    )
    apply_p.add_argument(
        "--rename-shifted",
        action="store_true",
        help="Optionally rename time-shifted BleeqUP files so their filename epoch matches corrected time",
    )
    apply_p.add_argument("--dry-run", action="store_true", help="Preview changes without modifying files")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    print(f"[ranger-sync] Command '{args.command}' initialized for: {args.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

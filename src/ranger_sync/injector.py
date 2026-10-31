"""Batch ExifTool metadata injection and shifted-file renaming."""

from __future__ import annotations

import subprocess
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import MediaItem
from .parser import find_exiftool


def _format_tz_offset(dt_local) -> str:
    """Format timezone offset as '+HH:MM' or '-HH:MM'."""
    raw = dt_local.strftime("%z")  # e.g. '+0100'
    if len(raw) == 5:
        return f"{raw[:3]}:{raw[3:]}"
    return "+00:00"


def _format_iso6709(lat: float, lon: float, ele: float | None) -> str:
    """Format coordinates as ISO 6709 string for QuickTime/Keys GPSCoordinates."""
    lat_sign = "+" if lat >= 0 else "-"
    lon_sign = "+" if lon >= 0 else "-"
    base = f"{lat_sign}{abs(lat):09.6f}{lon_sign}{abs(lon):010.6f}"
    if ele is not None:
        ele_sign = "+" if ele >= 0 else "-"
        return f"{base}{ele_sign}{abs(ele):06.2f}/"
    return f"{base}/"


def build_exiftool_args_for_item(
    item: MediaItem,
    tz: ZoneInfo,
    write_filesystem: bool = True,
    overwrite_original: bool = True,
) -> list[str]:
    """Generate ExifTool CLI arguments for a single BleeqUP MediaItem."""
    dt_utc = item.corrected_utc.astimezone(timezone.utc)
    dt_loc = dt_utc.astimezone(tz)

    utc_str = dt_utc.strftime("%Y:%m:%d %H:%M:%S")
    loc_str = dt_loc.strftime("%Y:%m:%d %H:%M:%S")
    tz_off = _format_tz_offset(dt_loc)
    loc_iso_tz = f"{loc_str}{tz_off}"
    ms_str = f"{item.corrected_epoch_ms % 1000:03d}"

    args: list[str] = []
    if overwrite_original:
        args.append("-overwrite_original")

    suffix = item.path.suffix.lower()
    is_video = suffix in (".mp4", ".mov")

    if is_video:
        args.extend(
            [
                "-Keys:Make=BleeqUP",
                "-Keys:Model=Ranger",
                f"-QuickTime:CreateDate={utc_str}",
                f"-QuickTime:ModifyDate={utc_str}",
                f"-TrackCreateDate={utc_str}",
                f"-TrackModifyDate={utc_str}",
                f"-MediaCreateDate={utc_str}",
                f"-MediaModifyDate={utc_str}",
                f"-Keys:CreationDate={loc_iso_tz}",
                f"-UserData:DateTimeOriginal={loc_iso_tz}",
            ]
        )
        if item.gpx_fix is not None:
            iso6709 = _format_iso6709(item.gpx_fix.lat, item.gpx_fix.lon, item.gpx_fix.ele)
            args.extend(
                [
                    f"-Keys:GPSCoordinates={iso6709}",
                    f"-UserData:GPSCoordinates={iso6709}",
                ]
            )
    else:
        # Photo (.JPG, .JPEG, .PNG — note BleeqUP .PNG files are often JPEG-encoded internally)
        args.extend(
            [
                "-IFD0:Make=BleeqUP",
                "-IFD0:Model=Ranger",
                f"-EXIF:DateTimeOriginal={loc_str}",
                f"-EXIF:CreateDate={loc_str}",
                f"-EXIF:ModifyDate={loc_str}",
                f"-EXIF:SubSecTimeOriginal={ms_str}",
                f"-EXIF:SubSecTimeDigitized={ms_str}",
                f"-EXIF:SubSecTime={ms_str}",
                f"-EXIF:OffsetTimeOriginal={tz_off}",
                f"-EXIF:OffsetTimeDigitized={tz_off}",
                f"-EXIF:OffsetTime={tz_off}",
                f"-XMP:DateCreated={loc_iso_tz}",
            ]
        )
        if item.matched_source_file:
            args.append(
                f"-EXIF:UserComment=BleeqUP AI Motion derived from {item.matched_source_file}"
            )
        if item.gpx_fix is not None:
            lat = item.gpx_fix.lat
            lon = item.gpx_fix.lon
            ele = item.gpx_fix.ele
            lat_ref = "N" if lat >= 0 else "S"
            lon_ref = "E" if lon >= 0 else "W"
            args.extend(
                [
                    f"-GPSLatitude={abs(lat):.8f}",
                    f"-GPSLatitudeRef={lat_ref}",
                    f"-GPSLongitude={abs(lon):.8f}",
                    f"-GPSLongitudeRef={lon_ref}",
                    f"-GPSDateStamp={dt_utc.strftime('%Y:%m:%d')}",
                    f"-GPSTimeStamp={dt_utc.strftime('%H:%M:%S')}",
                ]
            )
            if ele is not None:
                ele_ref = "0" if ele >= 0 else "1"
                args.extend(
                    [
                        f"-GPSAltitude={abs(ele):.2f}",
                        f"-GPSAltitudeRef={ele_ref}",
                    ]
                )

    # Set filesystem dates last so ExifTool doesn't clobber FileModifyDate when writing
    if write_filesystem:
        args.extend(
            [
                f"-FileCreateDate={loc_iso_tz}",
                f"-FileModifyDate={loc_iso_tz}",
            ]
        )

    args.append(str(item.path))
    return args


def apply_metadata_and_renames(
    items: list[MediaItem],
    tz: ZoneInfo,
    write_filesystem: bool = True,
    overwrite_original: bool = True,
    rename_shifted: bool = False,
    dry_run: bool = False,
) -> tuple[int, list[tuple[str, str]]]:
    """Batch-write metadata via ExifTool and optionally rename shifted files.

    Returns (updated_count, list of (old_filename, new_filename) renames).
    """
    bleequp_items = [i for i in items if i.is_bleequp]
    if not bleequp_items:
        return 0, []

    renames: list[tuple[str, str]] = []
    for item in bleequp_items:
        if rename_shifted and item.time_shift_sec != 0.0:
            new_name = item.new_filename_for_shift()
            if new_name != item.filename:
                renames.append((item.filename, new_name))

    if dry_run:
        return len(bleequp_items), renames

    exiftool_bin = find_exiftool()
    # Handle BleeqUP quirk where .PNG files are actually JPEG-encoded internally (magic \xff\xd8\xff)
    temp_jpeg_swaps: list[tuple[MediaItem, Path, Path]] = []
    for item in bleequp_items:
        if item.path.suffix.lower() == ".png" and item.path.is_file():
            with open(item.path, "rb") as fp:
                magic = fp.read(3)
            if magic == b"\xff\xd8\xff":
                orig_path = item.path
                tmp_path = item.path.with_name(item.path.name + ".tmp_exif.jpg")
                orig_path.rename(tmp_path)
                item.path = tmp_path
                temp_jpeg_swaps.append((item, orig_path, tmp_path))

    try:
        # Build argfile payload using -execute between files for single-process batch execution
        argfile_lines: list[str] = ["-charset", "filename=utf8"]
        for item in bleequp_items:
            file_args = build_exiftool_args_for_item(
                item=item,
                tz=tz,
                write_filesystem=write_filesystem,
                overwrite_original=overwrite_original,
            )
            argfile_lines.extend(file_args)
            argfile_lines.append("-execute")

        payload = "\n".join(argfile_lines) + "\n"
        proc = subprocess.run(
            [exiftool_bin, "-@", "-"],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"exiftool batch execution failed (exit {proc.returncode}):\n{proc.stderr}\n{proc.stdout}"
            )
    finally:
        for item, orig_path, tmp_path in temp_jpeg_swaps:
            if tmp_path.exists():
                tmp_path.rename(orig_path)
            item.path = orig_path

    # Perform renames after ExifTool finishes writing
    applied_renames: list[tuple[str, str]] = []
    if rename_shifted:
        for item in bleequp_items:
            if item.time_shift_sec != 0.0:
                new_name = item.new_filename_for_shift()
                if new_name != item.filename:
                    new_path = item.path.with_name(new_name)
                    if not new_path.exists():
                        item.path.rename(new_path)
                        applied_renames.append((item.filename, new_name))
                        item.path = new_path
                        item.filename = new_name

    return len(bleequp_items), applied_renames

"""Media file discovery and metadata extraction via ExifTool."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .models import MediaItem, MediaKind

# BleeqUP filename regexes (13-digit epoch milliseconds)
RE_BLEEQUP_PHOTO = re.compile(r"^IMG_(\d{13})\.(jpg|jpeg)$", re.IGNORECASE)
RE_BLEEQUP_VIDEO = re.compile(r"^VIDEO_(\d{13})\.(mp4|mov)$", re.IGNORECASE)
RE_BLEEQUP_AI_MOTION = re.compile(r"^BleeqUp_Motion_(\d{13})\.(png|jpg|jpeg)$", re.IGNORECASE)
RE_BLEEQUP_AI_VIDEO = re.compile(
    r"^(?:VIDEO_HIGHLIGHT_(\d{13})|BleeqUp_Remix_(\d{13})_ai)\.(mp4|mov)$",
    re.IGNORECASE,
)

# Reference iPhone media (e.g. IMG_8591.MOV, IMG_8602.HEIC)
RE_REF_IPHONE = re.compile(r"^IMG_\d{4,5}\.(mov|mp4|heic|jpg|jpeg)$", re.IGNORECASE)


def find_exiftool() -> str:
    """Locate the exiftool binary on macOS."""
    for candidate in (
        shutil.which("exiftool"),
        "/opt/homebrew/bin/exiftool",
        "/usr/local/bin/exiftool",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "exiftool not found. Install it via Homebrew: `brew install exiftool`"
    )


def classify_filename(name: str) -> tuple[MediaKind, int | None] | None:
    """Classify a filename into MediaKind and extract 13-digit epoch ms if present."""
    if m := RE_BLEEQUP_PHOTO.match(name):
        return MediaKind.BLEEQUP_PHOTO, int(m.group(1))
    if m := RE_BLEEQUP_VIDEO.match(name):
        return MediaKind.BLEEQUP_VIDEO, int(m.group(1))
    if m := RE_BLEEQUP_AI_MOTION.match(name):
        return MediaKind.BLEEQUP_AI_MOTION, int(m.group(1))
    if m := RE_BLEEQUP_AI_VIDEO.match(name):
        ms_str = m.group(1) or m.group(2)
        return MediaKind.BLEEQUP_AI_VIDEO, int(ms_str)
    if RE_REF_IPHONE.match(name):
        return MediaKind.REFERENCE_IPHONE, None
    return None


def _parse_exif_datetime_utc(val: str | None) -> datetime | None:
    """Parse ExifTool date string (with or without offset) into UTC datetime."""
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    # Format: "2026:10:31 09:15:39+01:00" or "2026:10:31 08:15:39Z" or "2026:10:31 08:15:39"
    m = re.match(
        r"^(\d{4}):(\d{2}):(\d{2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$",
        val,
    )
    if not m:
        return None
    year, month, day, hour, minute, sec, frac, tz_part = m.groups()
    iso_date = f"{year}-{month}-{day}T{hour}:{minute}:{sec}"
    if frac:
        iso_date += f".{frac[:6]}"
    if tz_part:
        if tz_part == "Z":
            iso_date += "+00:00"
        elif len(tz_part) == 5 and ":" not in tz_part:
            iso_date += f"{tz_part[:3]}:{tz_part[3:]}"
        else:
            iso_date += tz_part
    else:
        iso_date += "+00:00"
    try:
        dt = datetime.fromisoformat(iso_date)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def scan_media_directory(directory: Path) -> list[MediaItem]:
    """Scan directory for BleeqUP and reference iPhone media files and extract metadata."""
    candidates: list[tuple[Path, MediaKind, int | None]] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        res = classify_filename(entry.name)
        if res is not None:
            candidates.append((entry, res[0], res[1]))

    if not candidates:
        return []

    exiftool_bin = find_exiftool()
    cmd = [
        exiftool_bin,
        "-json",
        "-n",
        "-Duration",
        "-Software",
        "-CreationDate",
        "-DateTimeOriginal",
        "-SubSecDateTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-TrackCreateDate",
        "-GPSLatitude",
        "-GPSLongitude",
        "-GPSAltitude",
        *[str(c[0]) for c in candidates],
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    meta_list = json.loads(proc.stdout)
    meta_by_path: dict[str, dict] = {item["SourceFile"]: item for item in meta_list}

    items: list[MediaItem] = []
    for path, kind, epoch_ms in candidates:
        meta = meta_by_path.get(str(path), {})
        dur = float(meta.get("Duration") or 0.0)
        lat = meta.get("GPSLatitude")
        lon = meta.get("GPSLongitude")
        ele = meta.get("GPSAltitude")
        lat_f = float(lat) if isinstance(lat, (int, float)) else None
        lon_f = float(lon) if isinstance(lon, (int, float)) else None
        ele_f = float(ele) if isinstance(ele, (int, float)) else None

        init_shift_sec = 0.0
        init_shift_reason = None
        emb_atom_utc = (
            _parse_exif_datetime_utc(meta.get("SubSecDateTimeOriginal"))
            or _parse_exif_datetime_utc(meta.get("CreationDate"))
            or _parse_exif_datetime_utc(meta.get("DateTimeOriginal"))
            or _parse_exif_datetime_utc(meta.get("MediaCreateDate"))
            or _parse_exif_datetime_utc(meta.get("TrackCreateDate"))
            or _parse_exif_datetime_utc(meta.get("CreateDate"))
        )

        if epoch_ms is not None:
            raw_utc = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
            corrected_utc = raw_utc
            # If previously synced by bleequp-ranger-sync without --rename-shifted, honor embedded shifted time
            if str(meta.get("Software") or "") == "bleequp-ranger-sync" and emb_atom_utc is not None:
                diff_s = round(emb_atom_utc.timestamp() - (epoch_ms // 1000))
                if abs(diff_s) >= 2 and abs(diff_s) <= 86400:
                    corrected_utc = datetime.fromtimestamp(
                        (epoch_ms / 1000.0) + diff_s, tz=timezone.utc
                    )
                    init_shift_sec = float(diff_s)
                    init_shift_reason = f"existing_exif_shift({diff_s:+d}s)"
        else:
            # Reference iPhone media: prefer SubSecDateTimeOriginal, CreationDate, DateTimeOriginal, CreateDate
            dt_utc = emb_atom_utc
            if dt_utc is None:
                dt_utc = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            raw_utc = dt_utc
            corrected_utc = dt_utc

        items.append(
            MediaItem(
                path=path,
                filename=path.name,
                kind=kind,
                raw_epoch_ms=epoch_ms,
                raw_utc=raw_utc,
                corrected_utc=corrected_utc,
                time_shift_sec=init_shift_sec,
                shift_reason=init_shift_reason,
                duration_sec=dur,
                internal_create_utc=emb_atom_utc,
                existing_lat=lat_f,
                existing_lon=lon_f,
                existing_ele=ele_f,
            )
        )

    return items

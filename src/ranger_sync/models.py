"""Data models for ranger-sync."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path


class MediaKind(str, Enum):
    """Classification of media files in a ride directory."""

    BLEEQUP_PHOTO = "bleequp_photo"
    BLEEQUP_VIDEO = "bleequp_video"
    BLEEQUP_AI_MOTION = "bleequp_ai_motion"
    BLEEQUP_AI_VIDEO = "bleequp_ai_video"
    REFERENCE_IPHONE = "reference_iphone"


@dataclass(frozen=True)
class TrackPoint:
    """A single GPX trackpoint sorted by UTC epoch seconds."""

    timestamp: float  # UTC epoch seconds
    lat: float
    lon: float
    ele: float | None = None
    source_file: str = ""


@dataclass
class GpxFix:
    """Interpolated GPS position and diagnostic metadata for a media timestamp."""

    lat: float
    lon: float
    ele: float | None
    mode: str  # "interpolated", "stationary_stop", "boundary_clamp", "visual_match"
    nearest_gap_sec: float
    bracket_span_sec: float
    bracket_dist_m: float
    source_file: str


@dataclass
class MediaItem:
    """Represents a parsed media file (BleeqUP or reference iPhone)."""

    path: Path
    filename: str
    kind: MediaKind
    raw_epoch_ms: int | None
    raw_utc: datetime
    corrected_utc: datetime
    time_shift_sec: float = 0.0
    shift_reason: str | None = None
    duration_sec: float = 0.0
    internal_create_utc: datetime | None = None
    existing_lat: float | None = None
    existing_lon: float | None = None
    existing_ele: float | None = None
    matched_source_file: str | None = None
    matched_distance: float | None = None
    gpx_fix: GpxFix | None = None
    simultaneous_refs: list[str] = field(default_factory=list)
    overlap_conflicts: list[str] = field(default_factory=list)

    @property
    def is_bleequp(self) -> bool:
        return self.kind in (
            MediaKind.BLEEQUP_PHOTO,
            MediaKind.BLEEQUP_VIDEO,
            MediaKind.BLEEQUP_AI_MOTION,
            MediaKind.BLEEQUP_AI_VIDEO,
        )

    @property
    def is_ai_generated(self) -> bool:
        return self.kind in (MediaKind.BLEEQUP_AI_MOTION, MediaKind.BLEEQUP_AI_VIDEO)

    @property
    def corrected_epoch_ms(self) -> int:
        if self.raw_epoch_ms is not None and self.time_shift_sec == 0.0:
            return self.raw_epoch_ms
        return int(round(self.corrected_utc.timestamp() * 1000))

    def new_filename_for_shift(self) -> str:
        """Compute updated filename with corrected 13-digit epoch ms when shifted."""
        if self.raw_epoch_ms is None or self.time_shift_sec == 0.0:
            return self.filename
        old_ms_str = f"{self.raw_epoch_ms:013d}"
        new_ms_str = f"{self.corrected_epoch_ms:013d}"
        if old_ms_str in self.filename:
            return self.filename.replace(old_ms_str, new_ms_str, 1)
        return self.filename

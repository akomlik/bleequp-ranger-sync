"""GPX parsing, timeline indexing, and stationary-aware spatial interpolation."""

from __future__ import annotations

import bisect
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .models import GpxFix, TrackPoint


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in meters between two WGS84 coordinates."""
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


class GpxIndex:
    """Unified timeline index across one or more GPX files."""

    def __init__(self, points: list[TrackPoint]) -> None:
        # Sort and deduplicate by timestamp
        dedup: dict[float, TrackPoint] = {}
        for pt in sorted(points, key=lambda p: p.timestamp):
            dedup[pt.timestamp] = pt
        self.points: list[TrackPoint] = list(dedup.values())
        self.timestamps: list[float] = [p.timestamp for p in self.points]

    @classmethod
    def from_paths(cls, paths: list[Path]) -> GpxIndex:
        all_points: list[TrackPoint] = []
        for path in paths:
            if path.is_dir():
                gpx_files = sorted(path.glob("*.gpx")) + sorted(path.glob("*.GPX"))
            else:
                gpx_files = [path]
            for gpx_file in gpx_files:
                all_points.extend(cls._parse_gpx_file(gpx_file))
        return cls(all_points)

    @staticmethod
    def _parse_gpx_file(path: Path) -> list[TrackPoint]:
        points: list[TrackPoint] = []
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return points
        root = tree.getroot()
        # Handle both namespaced and non-namespaced GPX files
        for elem in root.iter():
            if not elem.tag.endswith("trkpt"):
                continue
            try:
                lat = float(elem.attrib["lat"])
                lon = float(elem.attrib["lon"])
            except (KeyError, ValueError):
                continue
            ele: float | None = None
            time_text: str | None = None
            for child in elem:
                if child.tag.endswith("ele") and child.text:
                    try:
                        ele = float(child.text.strip())
                    except ValueError:
                        pass
                elif child.tag.endswith("time") and child.text:
                    time_text = child.text.strip()
            if not time_text:
                continue
            try:
                dt = datetime.fromisoformat(time_text.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                points.append(
                    TrackPoint(
                        timestamp=dt.timestamp(),
                        lat=lat,
                        lon=lon,
                        ele=ele,
                        source_file=path.name,
                    )
                )
            except ValueError:
                continue
        return points

    @property
    def is_empty(self) -> bool:
        return len(self.points) == 0

    @property
    def start_utc(self) -> datetime | None:
        if not self.points:
            return None
        return datetime.fromtimestamp(self.points[0].timestamp, tz=timezone.utc)

    @property
    def end_utc(self) -> datetime | None:
        if not self.points:
            return None
        return datetime.fromtimestamp(self.points[-1].timestamp, tz=timezone.utc)

    def detect_pauses(self, min_gap_sec: float = 120.0) -> list[tuple[TrackPoint, TrackPoint, float, float]]:
        """Return list of (pt_a, pt_b, gap_seconds, distance_meters) for gaps >= min_gap_sec."""
        pauses = []
        for i in range(len(self.points) - 1):
            pa, pb = self.points[i], self.points[i + 1]
            dt = pb.timestamp - pa.timestamp
            if dt >= min_gap_sec:
                dist = haversine_m(pa.lat, pa.lon, pb.lat, pb.lon)
                pauses.append((pa, pb, dt, dist))
        return pauses

    def interpolate(
        self,
        target_ts: float,
        max_moving_gap_sec: float = 120.0,
        max_stationary_gap_sec: float = 7200.0,
        stationary_radius_m: float = 150.0,
        boundary_tolerance_sec: float = 300.0,
    ) -> GpxFix | None:
        """Interpolate WGS84 position at target_ts (UTC epoch seconds)."""
        if not self.points:
            return None

        # Before first GPX point
        if target_ts < self.timestamps[0]:
            gap = self.timestamps[0] - target_ts
            if gap <= boundary_tolerance_sec:
                p0 = self.points[0]
                return GpxFix(
                    lat=p0.lat,
                    lon=p0.lon,
                    ele=p0.ele,
                    mode="boundary_clamp",
                    nearest_gap_sec=gap,
                    bracket_span_sec=gap,
                    bracket_dist_m=0.0,
                    source_file=p0.source_file,
                )
            return None

        # After last GPX point
        if target_ts > self.timestamps[-1]:
            gap = target_ts - self.timestamps[-1]
            if gap <= boundary_tolerance_sec:
                pn = self.points[-1]
                return GpxFix(
                    lat=pn.lat,
                    lon=pn.lon,
                    ele=pn.ele,
                    mode="boundary_clamp",
                    nearest_gap_sec=gap,
                    bracket_span_sec=gap,
                    bracket_dist_m=0.0,
                    source_file=pn.source_file,
                )
            return None

        idx = bisect.bisect_left(self.timestamps, target_ts)
        if idx < len(self.points) and self.timestamps[idx] == target_ts:
            p = self.points[idx]
            return GpxFix(
                lat=p.lat,
                lon=p.lon,
                ele=p.ele,
                mode="interpolated",
                nearest_gap_sec=0.0,
                bracket_span_sec=0.0,
                bracket_dist_m=0.0,
                source_file=p.source_file,
            )

        pa = self.points[idx - 1]
        pb = self.points[idx]
        span_sec = pb.timestamp - pa.timestamp
        nearest_gap = min(target_ts - pa.timestamp, pb.timestamp - target_ts)
        dist_m = haversine_m(pa.lat, pa.lon, pb.lat, pb.lon)

        if nearest_gap <= max_moving_gap_sec:
            mode = "interpolated"
        elif dist_m <= stationary_radius_m and span_sec <= max_stationary_gap_sec:
            mode = "stationary_stop"
        else:
            return None

        alpha = (target_ts - pa.timestamp) / span_sec if span_sec > 0 else 0.0
        lat = pa.lat + alpha * (pb.lat - pa.lat)
        lon = pa.lon + alpha * (pb.lon - pa.lon)
        if pa.ele is not None and pb.ele is not None:
            ele: float | None = pa.ele + alpha * (pb.ele - pa.ele)
        else:
            ele = pa.ele if pa.ele is not None else pb.ele

        return GpxFix(
            lat=lat,
            lon=lon,
            ele=ele,
            mode=mode,
            nearest_gap_sec=nearest_gap,
            bracket_span_sec=span_sec,
            bracket_dist_m=dist_m,
            source_file=pa.source_file,
        )

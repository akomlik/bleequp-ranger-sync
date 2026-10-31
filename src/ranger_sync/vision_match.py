"""Perceptual image matching for BleeqUp_Motion_* AI images using macOS Vision."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import timedelta

from .models import GpxFix, MediaItem, MediaKind

SWIFT_VISION_SCRIPT = r"""
import Foundation
import Vision

let args = Array(CommandLine.arguments.dropFirst())
guard let sepIdx = args.firstIndex(of: "--") else {
    print("{}")
    exit(0)
}
let sources = Array(args[0..<sepIdx])
let targets = Array(args[(sepIdx + 1)...])

func featurePrint(for path: String) -> VNFeaturePrintObservation? {
    let url = URL(fileURLWithPath: path)
    let req = VNGenerateImageFeaturePrintRequest()
    let handler = VNImageRequestHandler(url: url, options: [:])
    try? handler.perform([req])
    return req.results?.first as? VNFeaturePrintObservation
}

var srcPrints: [(String, VNFeaturePrintObservation)] = []
for s in sources {
    if let fp = featurePrint(for: s) {
        srcPrints.append((s, fp))
    }
}

var matches: [String: [String: Any]] = [:]
for t in targets {
    if let tfp = featurePrint(for: t) {
        var bestPath = ""
        var bestDist: Float = 999.0
        for (s, sfp) in srcPrints {
            var dist: Float = 999.0
            if (try? tfp.computeDistance(&dist, to: sfp)) != nil {
                if dist < bestDist {
                    bestDist = dist
                    bestPath = s
                }
            }
        }
        if !bestPath.isEmpty {
            matches[t] = ["source": bestPath, "distance": bestDist]
        }
    }
}

if let data = try? JSONSerialization.data(withJSONObject: matches, options: []),
   let str = String(data: data, encoding: .utf8) {
    print(str)
} else {
    print("{}")
}
"""


def match_ai_motion_images(
    items: list[MediaItem],
    sync_time_to_source: bool = False,
    max_distance: float = 0.85,
) -> None:
    """Match BleeqUp_Motion_* images to their source IMG_* capture via macOS Vision."""
    sources = [i for i in items if i.kind == MediaKind.BLEEQUP_PHOTO]
    targets = [i for i in items if i.kind == MediaKind.BLEEQUP_AI_MOTION]
    if not sources or not targets or not shutil.which("swift"):
        return

    cmd = [
        "swift",
        "-e",
        SWIFT_VISION_SCRIPT,
        *[str(s.path) for s in sources],
        "--",
        *[str(t.path) for t in targets],
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        result = json.loads(proc.stdout.strip() or "{}")
    except Exception:
        return

    src_by_path: dict[str, MediaItem] = {str(s.path): s for s in sources}
    for target in targets:
        match_info = result.get(str(target.path))
        if not match_info:
            continue
        dist = float(match_info.get("distance", 999.0))
        src_path = match_info.get("source", "")
        src_item = src_by_path.get(src_path)
        if src_item is not None and dist <= max_distance:
            target.matched_source_file = src_item.filename
            target.matched_distance = dist
            if sync_time_to_source:
                target.corrected_utc = src_item.corrected_utc + timedelta(seconds=1)
                target.time_shift_sec = (target.corrected_utc - target.raw_utc).total_seconds()
                target.shift_reason = f"matched_source({src_item.filename}+1s)"
            if src_item.gpx_fix is not None:
                target.gpx_fix = GpxFix(
                    lat=src_item.gpx_fix.lat,
                    lon=src_item.gpx_fix.lon,
                    ele=src_item.gpx_fix.ele,
                    mode=f"visual_match({src_item.filename})",
                    nearest_gap_sec=src_item.gpx_fix.nearest_gap_sec,
                    bracket_span_sec=src_item.gpx_fix.bracket_span_sec,
                    bracket_dist_m=src_item.gpx_fix.bracket_dist_m,
                    source_file=src_item.gpx_fix.source_file,
                )

# bleeqUP Ranger Sync (`ranger-sync`)

Time-sync, clock-skew recovery, and GPX geotagging utility for **BleeqUP Ranger AI cycling glasses** media on macOS.

## Why This Tool Exists

Media exported from **BleeqUP Ranger** AI cycling glasses has several metadata challenges when mixed into a library with iPhone photos/videos (Apple Photos, Lightroom, Finder):

1. **Missing EXIF & QuickTime Capture Metadata:** Exported `.JPG` and `.MP4` files lack `EXIF:DateTimeOriginal`, camera `Make`/`Model`, and GPS coordinates, relying only on a 13-digit UTC millisecond epoch embedded in the filename (e.g. `IMG_1793437400537.JPG`, `VIDEO_1793434415483.MP4`).
2. **AirDrop / App Export Timestamp Stripping:** Exporting from the BleeqUP iOS app or transferring via AirDrop sets the filesystem creation/modification dates (`btime`/`mtime`) and QuickTime `mvhd` creation time to the **export/transfer time** (often in reverse chronological order).
3. **Pre-Sync Clock Skew / DST Jump ("The 1-Hour Out-of-Order Bug"):** BleeqUP Ranger glasses only sync their internal Real-Time Clock (RTC) when connected via Bluetooth to the BleeqUP phone app. If you start recording a ride before opening the app (especially after a Daylight Saving Time transition or multi-day standby), early clips carry a `+1h` (plus RTC quartz drift) filename timestamp. Once the glasses connect to the phone mid-ride, the clock snaps back, causing early-ride files to sort **in the middle of the ride** and overlap mid-ride recordings.
4. **AI-Generated Media Post-Ride Timestamps:**
   - **AI Stylized Photos (`BleeqUp_Motion_<epoch>.PNG/.jpg`):** Generated in the app hours after the ride; their filename epoch reflects generation time rather than capture time. `ranger-sync` uses macOS `Vision` (`VNGenerateImageFeaturePrintRequest`) perceptual feature matching to pair each AI Motion image with its original `IMG_<epoch>` source capture and inherit its capture timestamp (`+1s`) and GPS fix.
   - **AI Highlight & Remix Videos (`VIDEO_HIGHLIGHT_<epoch>.MP4`, `BleeqUp_Remix_<epoch>_ai.MP4`):** Post-ride montages that need ride-level geotagging and chronological placement.
5. **Cycling Computer Auto-Pause GPX Gaps:** Standard GPX geotaggers fail when a media timestamp falls inside a 30-minute cafe stop where the cycling head unit paused recording. `ranger-sync` distinguishes **stationary pause gaps** (large time gap $\Delta t$, small spatial displacement $\Delta d \le 150\text{ m}$) from true out-of-track gaps.

---

## Prerequisites (macOS)

- **macOS 14+** (uses native `Vision` & `AVFoundation` via Swift helper for perceptual matching and QuickTime inspection)
- **Python 3.10+**
- **ExifTool** (for atomic batch writing of EXIF, QuickTime, Keys, and filesystem timestamps):
  ```bash
  brew install exiftool
  ```

---

## CLI Workflow

### 1. Inspect & Diagnose a Ride Folder
Scans a mixed media folder (BleeqUP + iPhone reference shots + `.gpx` tracks), detects timeline overlaps (clock skew / DST jump), matches `BleeqUp_Motion_*` images to their source photos, and identifies simultaneous dual-camera recordings:

```bash
ranger-sync inspect "/Users/akomlik/Desktop/Mallorca-2026-Fall/2026-10-31_PCH,Can_Picafort"
```

### 2. Sync & Geotag Media (Dry Run / Apply)
```bash
ranger-sync apply "/Users/akomlik/Desktop/Mallorca-2026-Fall/2026-10-31_PCH,Can_Picafort" \
  --timezone "Europe/Madrid" \
  --shift-files "VIDEO_1793437368668.MP4,IMG_1793437400537.JPG,IMG_1793437486125.jpg,IMG_1793437637238.jpg,VIDEO_1793437646586.MP4,VIDEO_1793437709396.MP4,VIDEO_1793437876579.MP4:-3837s" \
  --match-ai-motion \
  --write-filesystem \
  --dry-run
```

See [`docs/DESIGN.md`](docs/DESIGN.md) for the complete architecture and forensic analysis.

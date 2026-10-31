# BleeqUP Ranger Sync (`ranger-sync`)

Time-sync, clock-skew recovery, optical/camera EXIF enrichment, and GPX geotagging utility for [**BleeqUP Ranger AI Sports Camera Glasses**](https://www.bleequp.com/products/bleequp-ranger-ai-sports-camera-glasses) media on macOS.

> **Note:** BleeqUP support has confirmed that native EXIF & GPS metadata embedding is on their roadmap for a future firmware/app release. Until that arrives—and for recovering from pre-Bluetooth-sync RTC/DST clock jumps—`ranger-sync` bridges the gap so your cycling photos and clips integrate seamlessly with Apple Photos, DaVinci Resolve, Lightroom, and Finder alongside iPhone media.

## Why This Tool Exists

Media exported from [**BleeqUP Ranger**](https://www.bleequp.com/products/bleequp-ranger-ai-sports-camera-glasses) AI cycling glasses has several metadata challenges when mixed into a library with iPhone photos/videos (Apple Photos, DaVinci Resolve, Lightroom, Finder):

1. **Missing EXIF, Camera/Lens Specs & Overwritten QuickTime Capture Metadata:**
   - Exported `.JPG` photos (`4656 × 3496` from the 16MP `1/3.09"` CMOS sensor) lack `EXIF:DateTimeOriginal`, camera `Make`/`Model`, lens optical specifications (`120°` ultra-wide POV lens, `1.8mm` / `14mm` 35mm-eq.), and GPS coordinates.
   - Exported `.MP4` videos (`2880 × 1620` @ 30fps with EIS) have their `Keys:CreationDate` and QuickTime `CreateDate` overwritten by the iOS save/export timestamp (e.g., `13:12`–`13:21` in reverse chronological order), while the true UTC capture epoch in milliseconds is only preserved in the filename (`IMG_1793437400537.JPG`, `VIDEO_1793434415483.MP4`).
2. **Pre-Sync Clock Skew / DST Jump ("The 1-Hour Out-of-Order Bug"):**
   - BleeqUP Ranger glasses only sync their internal Real-Time Clock (RTC) when connected via Bluetooth to the BleeqUP iPhone app.
   - If you start recording a ride before the glasses sync with the app (especially after a Daylight Saving Time change or multi-day standby), early clips carry a `+1h` (plus RTC quartz drift) filename timestamp. Once the glasses connect to the phone mid-ride, the clock snaps backward, causing early-ride files to sort **in the middle of the ride** and overlap mid-ride recordings.
3. **AI-Generated Media (`BleeqUp_Motion_*`, `VIDEO_HIGHLIGHT_*`, `BleeqUp_Remix_*`):**
   - Keeps AI-generated files at their post-ride generation timestamp by default (configurable via `--sync-ai-motion-time`), while using macOS `Vision` (`VNGenerateImageFeaturePrintRequest`) perceptual feature matching to automatically pair `BleeqUp_Motion_<epoch>.PNG/.jpg` images with their source `IMG_<epoch>` capture and inherit its exact GPS coordinates.
4. **Cycling Computer Auto-Pause GPX Gaps:**
   - Standard GPX geotaggers fail when a media timestamp falls inside a 30-minute cafe stop where the cycling head unit paused recording. `ranger-sync` distinguishes **stationary pause gaps** (large time gap $\Delta t$, small spatial displacement $\Delta d \le 150\text{ m}$) from true out-of-track gaps.

---

## Installation (macOS)

1. Install **ExifTool** via Homebrew:
   ```bash
   brew install exiftool
   ```
2. Install `bleequp-ranger-sync` (editable mode for development):
   ```bash
   cd ~/src/bleequp-ranger-sync
   pip3 install --user -e .
   ```
   Or run directly via `python3 -m ranger_sync.cli` from `~/src/bleequp-ranger-sync/src`.

---

## Usage

### 1. Inspect & Diagnose a Ride Folder
Scans a mixed media folder (BleeqUP + iPhone reference shots + `.gpx` tracks), detects physical timeline overlaps (clock skew / DST jump), matches `BleeqUp_Motion_*` images to their source photos, and identifies simultaneous dual-camera recordings:

```bash
ranger-sync inspect "/Users/akomlik/Desktop/Mallorca-2026-Fall/2026-10-31_PCH,Can_Picafort"
```

### 2. Sync & Geotag Media (`--dry-run` / Apply)
Preview or apply EXIF/QuickTime metadata injection, macOS Finder creation/modification dates (`btime`/`mtime`), and optional filename epoch renaming (`--rename-shifted`) for time-shifted files:

```bash
ranger-sync apply "/Users/akomlik/Desktop/Mallorca-2026-Fall/2026-10-31_PCH,Can_Picafort" \
  --timezone "Europe/Madrid" \
  --shift-files "VIDEO_1793437368668.MP4,IMG_1793437400537.JPG,IMG_1793437486125.jpg,IMG_1793437637238.jpg,VIDEO_1793437646586.MP4,VIDEO_1793437709396.MP4,VIDEO_1793437876579.MP4:-3837s" \
  --rename-shifted \
  --dry-run
```

Remove `--dry-run` to write metadata and rename the shifted files in place.

---

## Running Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

See [`docs/DESIGN.md`](docs/DESIGN.md) for the complete architecture, metadata matrix, and forensic analysis (including iPhone Finder `Files -> BleeqUp` container artifacts).

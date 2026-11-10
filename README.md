# BleeqUP Ranger Sync (`ranger-sync`)

Time-sync, clock-skew recovery, optical/camera EXIF enrichment, and GPX geotagging utility for [**BleeqUP Ranger AI Sports Camera Glasses**](https://www.bleequp.com/products/bleequp-ranger-ai-sports-camera-glasses) media on macOS.

> **Note:** BleeqUP support has confirmed that native EXIF & GPS metadata embedding is on their roadmap for a future firmware/app release. Until that arrives—and for recovering from pre-Bluetooth-sync RTC/DST clock jumps—`ranger-sync` bridges the gap so your cycling photos and clips integrate seamlessly into **Apple Photos, DaVinci Resolve, Final Cut Pro, iMovie, Lightroom, and Finder** alongside iPhone and action camera footage.

---

## Quickstart (The One-Liner)

Drop your BleeqUP clips and your bike computer's `.gpx` file into the same folder:

```text
2026-10-31_Mallorca_Ride/
├── activity.gpx                       # Export from Garmin Edge, Wahoo, Strava, etc.
├── IMG_1793437400537.JPG              # BleeqUP photo (epoch ms in filename, no EXIF)
├── VIDEO_1793434415483.MP4            # BleeqUP 3K video (creation time overwritten)
└── IMG_8594.MOV                       # Optional iPhone reference clip (with GPS)
```

Run one command:

```bash
ranger-sync apply ./2026-10-31_Mallorca_Ride
```

`ranger-sync` will:
1. **Auto-detect & solve clock skew:** Compensates for deep-sleep oscillator drift (e.g., `-2482s`) or mid-ride Camera Settings reconnects (`-3837s`) against your GPX timeline.
2. **Inject QuickTime & EXIF metadata:** Writes proper `QuickTime:CreateDate`, Apple `Keys:CreationDate`, `DateTimeOriginal`, and camera/lens specs (`120°` ultra-wide POV, `14mm` eq.).
3. **Embed GPS coordinates & altitude:** Geotags every photo and video from your GPX track (intelligently handling auto-pause cafe stops).
4. **Update macOS Finder & rename files:** Sets Finder file creation dates (`btime`/`mtime`) and automatically updates filename epoch timestamps so files sort in 100% chronological order in DaVinci Resolve, Final Cut Pro, and Finder.

---

## Why This Tool Exists

Anyone looking to bring BleeqUP footage into standard NLEs like **DaVinci Resolve, Apple Final Cut Pro, or iMovie** immediately hits several walls:

1. **Broken Multi-Cam Alignment & Chronological Sorting:**
   - BleeqUP exports `.MP4` files where QuickTime `CreateDate` and `Keys:CreationDate` are overwritten with the iOS download/export timestamp (often in reverse chronological order). NLE timelines and Apple Photos sort clips by export time rather than capture time.
2. **Missing EXIF, Camera/Lens Specs & Geodata:**
   - Exported `.JPG` photos (`4656 × 3496` from the 16MP `1/3.09"` CMOS sensor) lack `EXIF:DateTimeOriginal`, camera `Make`/`Model`, lens optical specifications (`120°` ultra-wide POV lens, `1.8mm` / `14mm` 35mm-eq.), and GPS coordinates.
3. **Pre-Sync Clock Skew & Severe Deep-Sleep Oscillator Drift (~11.3 Minutes/Day!):**
   - BleeqUP Ranger glasses have two separate internal subsystems: the **BLE audio/control channel** (used by "AI-Tracking") and the **Camera SoC** (which timestamps `IMG_*` and `VIDEO_*` files using its own internal RTC).
   - Merely connecting the glasses via Bluetooth or starting "AI-Tracking" in the app **does not sync the Camera SoC's clock**! The Camera SoC only syncs its clock with your iPhone when you open **Camera Settings** (e.g., toggling `1080p` $\leftrightarrow$ `3K` video mode) or trigger a camera/gallery handshake in the app.
   - **Why the internal clock drifts so fast (`+2482s` on Oct 29 $\rightarrow$ `+3837s` on Oct 31):**
     - Across two Mallorca rides taken **46 hours apart** (Thursday Oct 29 to Saturday Oct 31, 2026) without opening Camera Settings in between, the glasses' internal clock offset grew from **`+2,482s` (`+41m 22s`)** on Oct 29 to **`+3,837s` (`+63m 57s`)** on Oct 31 — an additional **`+1,355s` (`+22m 35s`) of drift in 46 hours**, or **`~11.3 minutes (+707 seconds) per day`** ($\approx +0.82\%$ frequency error).
     - This happens because when the main Camera SoC powers down in standby, timekeeping falls back to a low-power **internal RC sleep oscillator** ($\pm 0.8\%\text{–}1\%$ tolerance) rather than a precision temperature-compensated $32.768\text{ kHz}$ quartz crystal. After just 3.5 days in standby, the clock is already 41 minutes fast; after 5.5 days, it is over an hour fast!
   - If you start recording a ride before the Camera SoC handshakes with the app, early clips carry that accumulated multi-day drift in their filename timestamp. If you then open Camera Settings mid-ride, the clock snaps backward to real time, causing early-ride files to sort **in the middle of the ride** and overlap mid-ride recordings.
   - `ranger-sync` auto-detects this skew against your GPX track and corrects it automatically.
4. **AI-Generated Media (`BleeqUp_Motion_*`, `VIDEO_HIGHLIGHT_*`, `BleeqUp_Remix_*`):**
   - Keeps AI-generated files at their post-ride generation timestamp by default (configurable via `--sync-ai-motion-time`), while using macOS `Vision` (`VNGenerateImageFeaturePrintRequest`) perceptual feature matching to automatically pair `BleeqUp_Motion_<epoch>.PNG/.jpg` images with their source `IMG_<epoch>` capture and inherit its exact GPS coordinates.
5. **Cycling Computer Auto-Pause GPX Gaps:**
   - Standard GPX geotaggers fail when a media timestamp falls inside a 30-minute cafe stop where the cycling head unit paused recording. `ranger-sync` distinguishes **stationary pause gaps** (large time gap $\Delta t$, small spatial displacement $\Delta d \le 150\text{ m}$) from true out-of-track gaps.

---

## Best Practices: Pre-Ride Field Workflow (Garmin Edge 1040 + BleeqUP Ranger)

### Why Connecting the App Wasn't Enough (And How to Avoid Time Shifts)
Across two rides in Mallorca, forensic comparison against Garmin Edge 1040 GPX tracks and iPhone reference clips exposed how the glasses' Camera SoC clock behaves when not explicitly woken before a ride:

| Ride Date | Camera SoC Clock Offset | How the Offset Was Verified | Drift Rate Analysis |
| :--- | :--- | :--- | :--- |
| **Thu Oct 29, 2026** (`Coll de sa Batalla`) | **`+2,482s` (`+41m 22s`)** across all 18 clips | In `VIDEO_1793273211837.MP4` (raw filename `12:26:51 CET`, filmed at the Coll de sa Batalla summit cafe stop right after iPhone clip `IMG_8527.MOV` at `11:44:14 CET`), an iPhone lock screen lying face-up on the table in the video frame clearly reads **`Thu Oct 29 11:45`** (`12:26:51 - 11:45:29 = +2482s`). Shifting all 18 clips by `-2482s` aligned the final clip (`VIDEO_1793276331971.MP4` $\rightarrow$ `12:37:29 CET`) within 8 seconds of the Garmin GPX stop at Pro Cycle Hire (`12:37:37 CET`). | ~3.5 days of standby drift since Sunday Oct 25 (`~707s/day`). |
| **Sat Oct 31, 2026** (`PCH, Can Picafort`) | **`+3,837s` (`+63m 57s`)** on first 7 captures, then **`0s`** after `09:08 CET` | Glasses were connected to the app running **AI-Tracking** from `08:43 CET`, yet the first 7 captures had a `+3,837s` offset until **Camera Settings** were opened at Pro Cycle Hire (`09:08 CET`) to switch video mode from `1080p` to `3K` (remarkably, those first 4 videos were *already* recorded in `2880×1620` 3K by the hardware—the app UI simply displayed a stale `1080p` default because it hadn't queried the Camera SoC yet!). | `+1,355s` (`+22m 35s`) additional drift accumulated in the **46 hours** since the Oct 29 ride (**`+11.3 min/day`**). |

To guarantee accurate timestamps on every ride:

1. **Perform a 10-Second Pre-Ride Camera Handshake Before Every Ride (Recommended):**
   - Because the sleep oscillator drifts by **~11–12 minutes per day** in standby, power on the glasses before rolling out, open the **BleeqUP app**, and **open the Camera Settings screen** (verify/toggle `3K` video mode or open the glasses gallery).
   - This forces the app to wake the **Camera SoC** (not just the BLE tracking service) and snap its internal RTC clock to your iPhone's exact millisecond time.
2. **Skip BleeqUP "AI-Tracking" & Rely on Your Garmin Edge 1040:**
   - You do **not** need to run BleeqUP's in-app AI-Tracking during the ride (which drains iPhone battery and splits GPX files on cafe stops).
   - Record your ride normally on your **Garmin Edge 1040** (which captures superior multi-band GNSS, barometric elevation, speed, heart rate, cadence, and power). Export the `.gpx` (or `.fit` $\rightarrow$ `.gpx`) into your ride folder and run `ranger-sync`—and use your Garmin telemetry directly in DaVinci Resolve / Telemetry Overlay for high-bitrate 3K HUD overlays.
3. **Avoid Mid-Ride Camera Settings Changes if Unsynced:**
   - If you ever start a ride *without* opening Camera Settings beforehand, all clips will share a single uniform offset (like the `-2482s` offset on Oct 29) as long as you don't open Camera Settings mid-ride.
   - **Pro Tip (Visual Time Anchor):** Snap one quick photo where your **Garmin Edge 1040 or iPhone lock-screen clock** is visible in the frame, or take one simultaneous clip with your iPhone; if you forgot to sync the glasses before the ride, that single visual anchor gives you the exact second offset for `--shift-files "*:<offset>" --rename-shifted`.

---

## Prerequisites & Installation (macOS)

### 1. Prerequisites
`ranger-sync` uses **ExifTool** under the hood to read and write QuickTime atoms, EXIF, and macOS filesystem creation dates. Install it via Homebrew:

```bash
brew install exiftool
```

### 2. Install `bleequp-ranger-sync`
Clone and install the package locally:

```bash
git clone https://github.com/akomlik/bleequp-ranger-sync.git ~/src/bleequp-ranger-sync
cd ~/src/bleequp-ranger-sync
pip3 install --user -e .
```

The `ranger-sync` command is now available in your terminal.

---

## Usage

### 1. Inspect & Diagnose a Ride Folder
Scans a mixed media folder (BleeqUP + iPhone reference shots + `.gpx` tracks), detects physical timeline overlaps (clock skew / DST jump), matches `BleeqUp_Motion_*` images to their source photos, and identifies simultaneous dual-camera recordings:

```bash
ranger-sync inspect "/path/to/ride_folder"
```

### 2. Sync & Geotag Media (`apply`)

#### Standard One-Liner (Automatic Mode)
With automatic clock skew detection (`--auto-shift`) and file renaming (`--rename-shifted`) enabled by default, you can simply run:

```bash
ranger-sync apply "/path/to/ride_folder"
```

To preview changes first without modifying files on disk, add `--dry-run`:

```bash
ranger-sync apply "/path/to/ride_folder" --dry-run
```

#### Manual Override (`--shift-files`)
If you ever want to override the automatic solver with an explicit manual clock offset (e.g. from an in-frame clock photo or simultaneous iPhone reference shot), pass `--shift-files`:

```bash
# Shift all MP4 videos by -2482s:
ranger-sync apply "/path/to/ride_folder" --shift-files "VIDEO_*.MP4:-2482s"

# Shift specific pre-sync clips by -3837s:
ranger-sync apply "/path/to/ride_folder" \
  --shift-files "VIDEO_1793437368668.MP4,IMG_1793437400537.JPG,...:-3837s"
```

---

## Running Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

See [`docs/DESIGN.md`](docs/DESIGN.md) for the complete architecture, metadata matrix, and forensic analysis (including iPhone Finder `Files -> BleeqUp` container artifacts).

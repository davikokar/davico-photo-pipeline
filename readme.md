
# Davico Photo Pipeline

Automated post-processing pipeline for HDR and panoramic photography, built for
mixed **aerial (DJI)** and **terrestrial (Canon)** shoots.

Given a folder of photos, the pipeline detects how the shots relate to each other
(single, HDR bracket, panorama, or HDR-within-panorama), then drives them through
RAW conversion, alignment, ghost detection, HDR merging, ghost application,
panorama stitching and final finishing (geometry, crop, optics, color, cleanup).

The pipeline is **session-based** and **resumable**: every step records its status in
`state.json`, so an interrupted run can be resumed and any single step can be
re-run without redoing the whole session. Selected checkpoints pause for **human
review** (grouping, post-HDR, final).

---

## Table of contents

- [Quick start](#quick-start)
- [Concepts](#concepts)
- [Pipeline steps](#pipeline-steps)
- [Group JSON model](#group-json-model)
- [CLI reference](#cli-reference)
- [Configuration](#configuration)
- [External tools](#external-tools)
- [Development setup](#development-setup)
- [Project layout](#project-layout)
- [Roadmap / TODO](#roadmap--todo)

---

## Quick start

```bash
# 1. Activate the environment (see Development setup)
source .venv/Scripts/activate

# 2. Process a folder of photos (JPGs) with an optional RAW folder
cd src
python run.py process /path/to/session_jpgs /path/to/session_raws

# 3. When the pipeline pauses at the grouping review point:
#    - open the generated groups_review.html in a browser
#    - adjust the grouping, export a corrected groups_NNN.json into the session dir
#    - return to the terminal and confirm to continue
```

---

## Concepts

**Capture source** — each group is either `aerial` (DJI) or `terrestrial` (Canon).
This drives which steps run: RAW conversion, alignment and ghost detection are
Canon-only.

**Group types**

| Type           | Meaning                                    |
|----------------|--------------------------------------------|
| `single`       | one standalone shot                        |
| `hdr`          | 3–5 bracketed exposures                     |
| `panorama`     | 2–n adjacent shots to be stitched          |
| `hdr+panorama` | multiple HDR brackets forming a panorama   |

**Session** — a run against one input folder. Lives under `workspace/<session_id>/`
and contains `state.json`, versioned `groups_NNN.json`, logs, and intermediate
outputs.

**Review points** — the pipeline stops for manual confirmation at:
`after_grouping`, `after_hdr`, `after_final`.

---

## Pipeline steps

The overall flow (steps are skipped when not applicable to a group):

```
grouping
  └─ human review
raw_to_jpg          (terrestrial + RAW available)
align               (terrestrial HDR + RAW available)
ghost_detection     (terrestrial HDR + RAW available)
hdr_merge           (HDR groups only)
ghost_application   (HDR groups with a ghost map)
stitch              (panorama groups only)
  └─ human review (post-HDR)
geometry → crop → optics → color → cleanup
  └─ human review (final)
```

### 1. Grouping

A folder of JPGs is analysed and grouped into `single`, `panorama`, `hdr` or
`hdr+panorama` groups. For HDR groups the per-shot exposure is recorded and the
central (middle-exposure / reference) shot is identified.

Grouping uses EXIF timing, focal length and EV variation, plus an optional visual
panorama check (LoFTR feature matching + MAGSAC++ homography) to confirm overlap.

The step writes a versioned `groups_NNN.json` and an interactive
`groups_review.html`. The pipeline pauses so the user can correct the grouping and
drop a new `groups_NNN.json` into the session directory; the highest-numbered file
is used downstream.

### 2. RAW → JPG conversion

Skipped for aerial DJI shots. For Canon shots with RAW files, DPP4 CLI renders each
RAW through multiple exposure recipes (`0`, `-2`, `+2`) to synthesise the exposures
needed for HDR and ghost handling.

- **Non-HDR shot** → converted with all recipes to produce 3 exposures.
- **HDR bracket** → each shot converted with recipe `0`; the reference shot is also
  rendered at `-2`/`+2` (the *noghost* set), and the under/over-exposed shots are
  rendered with the opposite recipe to produce exposure-*normalized* copies.

| Image      | Exposure | Recipe 0 | Recipe -1 | Recipe +1 | Recipe -2 | Recipe +2 |
|------------|----------|----------|-----------|-----------|-----------|-----------|
| IMG_01.CR2 |    0     |    x     |    x      |    x      |    x      |    x      |
| IMG_02.CR2 |   -1     |    x     |           |    x      |           |           |
| IMG_03.CR2 |   +1     |    x     |    x      |           |           |           |
| IMG_04.CR2 |   -2     |    x     |           |           |           |    x      |
| IMG_05.CR2 |   +2     |    x     |           |           |    x      |           |

A 3-shot HDR group yields 7 converted images; a 5-shot group yields 13.

### 3. Alignment & ghost detection

Runs only for **terrestrial HDR groups with RAW available**. It aligns the bracket
and produces a ghost map used later to blend a high-dynamic-range merge with a
ghost-free merge.

Skipped for aerial HDR (long shooting distance minimises ghosts; alignment is left
to the HDR merge) and for HDR groups without RAW (no exposure-normalized images, so
detection would be unreliable).

### 4. HDR merge

Runs for HDR groups via Photomatix CLI. Two scenarios:

- **With ghost map** — a "normal" merge (best dynamic range, worst ghosting) and a
  "noghost" merge (single-RAW, ghost-free) are both generated and blended using the
  ghost map for best dynamic range with minimal ghosting.
- **Without ghost map** — a single merge from the bracketed shots.

By default three styles are generated per group: **natural**, **realistic** and
**photographic** (styles are configurable in `pipeline.yaml`).

### 5. Ghost application

Blends the "normal" and "noghost" merges using the ghost map produced in step 3.

### 6. Stitching & finishing

Panorama groups are stitched (Hugin/OpenCV), followed by `geometry`, `crop`,
`optics`, `color` and `cleanup` finishing steps before the final review point.

---

## Group JSON model

Each processing stage augments the versioned group JSON. An HDR group looks like:

```jsonc
{
  "id": "group_003",
  "type": "hdr",
  "brackets": [
    {
      "shots": [
        { "filename": "0H8A4495.JPG", "ev": 13.88, "shutter": 0.008,  "step_offset":  0, "reference_shot": true  },
        { "filename": "0H8A4496.JPG", "ev": 15.88, "shutter": 0.002,  "step_offset": -2, "reference_shot": false },
        { "filename": "0H8A4497.JPG", "ev": 11.92, "shutter": 0.0125, "step_offset":  2, "reference_shot": false }
      ],
      "noghost": [
        { "filename": "0H8A4495_+2.JPG" },
        { "filename": "0H8A4495_-2.JPG" }
      ],
      "normalized": [
        { "filename": "0H8A4496_-2.JPG" },
        { "filename": "0H8A4497_+2.JPG" }
      ]
    }
  ]
}
```

More examples live in [`docs/json_examples/`](docs/json_examples).

---

## CLI reference

Run from the `src/` directory. All commands accept `--config <path>` (defaults to
`config/pipeline.yaml`).

| Command   | Description                              | Example |
|-----------|------------------------------------------|---------|
| `process` | Start a new session from source folders  | `python run.py process ./session_jpgs ./session_raws` |
| `resume`  | Resume an interrupted session            | `python run.py resume ./workspace/20250306_101500` |
| `rerun`   | Re-run one step for one group            | `python run.py rerun ./workspace/20250306_101500 --group group_001 --step color` |
| `status`  | Print the session state summary          | `python run.py status ./workspace/20250306_101500` |

Notes:
- `process` takes two positional arguments: the JPG input folder and the RAW folder
  (pass an existing path; if it does not exist, RAW steps are skipped).
- Valid `--step` values match the pipeline steps: `grouping`, `raw_to_jpg`,
  `hdr_merge`, `ghost_application`, `stitch`, `geometry`, `crop`, `optics`, `color`,
  `cleanup`.

---

## Configuration

All behaviour is driven by [`src/config/pipeline.yaml`](src/config/pipeline.yaml).
Key sections:

- `pipeline` — workspace/output folders and whether intermediates are kept.
- `grouper` — exiftool path, HDR/panorama gap thresholds, focal-length tolerance and
  the optional visual panorama check (`pano_visual_check`, `pano_check`).
- `steps.hdr.raw_to_jpg` — DPP4 CLI path, JPEG quality, RAW extensions and recipe
  file mapping.
- `steps.hdr.aligner` / `ghost_detector` — alignment diagnostics and ghost-detection
  tuning (thresholds, SSIM scales, chroma weighting, clipping).
- `steps.hdr.merging` — Photomatix executable, styles to generate, ghost thresholds
  and per-style tone parameters.
- `stitch`, `geometry`, `crop`, `optics`, `color`, `cleanup` — finishing options.
- `notifications`, `review_points` — completion notifications and which review
  checkpoints are active.

Recipe/preset files referenced by the config live in
[`src/config/hdr/`](src/config/hdr).

---

## External tools

These must be installed and their paths set in `pipeline.yaml`.

| Tool | Purpose | Config key |
|------|---------|------------|
| **exiftool** | Read EXIF for grouping/exposure | `grouper.exitool_exe` |
| **Canon DPP4 CLI** (`dpp4cli`) | RAW → JPG with exposure recipes | `steps.hdr.raw_to_jpg.dpp4cli_exe` |
| **Photomatix Pro CLI** | HDR merging | `steps.hdr.merging.photomatix_exe` |
| **Hugin** (or OpenCV) | Panorama stitching | `stitch.tool` |

**RawTherapee** is a supported alternative RAW converter. Example CLI usage:

```bash
rawtherapee-cli -p profile_exposure_0.pp3  -o 0H8A4482_0.jpg  -c 0H8A4482.CR3
rawtherapee-cli -p profile_exposure_-2.pp3 -o 0H8A4482_-2.jpg -c 0H8A4482.CR3
rawtherapee-cli -p profile_exposure_+2.pp3 -o 0H8A4482_+2.jpg -c 0H8A4482.CR3
```

Photomatix CLI examples for each style:

```bash
# Realistic
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -5 -5a 0.0 -5c 0.0 -5h 2.0 img1.jpg img2.jpg img3.jpg

# Photographic (requires an .xmp preset)
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -3 -t2 -x2 photographic.xmp img1.jpg img2.jpg img3.jpg

# Adjusted / Natural
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -2 -2a 5.0 -2b -6.0 -2c 2.0 -2h 5.0 -2k 6.0 -2m 2.0 -2s 6.0 -2w 6.0 img1_s.jpg img2_s.jpg img3_s.jpg
```

---

## Development setup

Requires Python 3.11+ and [uv](https://github.com/astral-sh/uv).

```bash
# create and activate a virtual environment
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); use .venv/bin/activate on Linux/macOS

# install uv and sync dependencies from src/pyproject.toml
pip install uv
cd src
uv sync --active
```

Run the tests (config in [`pytest.ini`](pytest.ini)):

```bash
pytest              # all tests
pytest test/unit    # unit tests only
```

Lint/format with ruff:

```bash
ruff check .
ruff format .
```

---

## Project layout

```
src/
  run.py                 # CLI entry point (process / resume / rerun / status)
  config/pipeline.yaml   # all pipeline configuration
  config/hdr/            # DPP4 recipes + Photomatix presets
  pipeline/
    orchestrator.py      # step scheduling + review points
    state.py             # session state (state.json) model
    steps/
      grouping/          # detection, HTML review, IO
      hdr/               # raw_to_jpg, aligner, ghost_detector, merger,
                         # ghost_application, exif_restore
    utils/               # exif, logging
docs/json_examples/      # sample group JSON per step
test/                    # unit + integration tests
```

---

## Roadmap / TODO

- Improve panorama grouping for terrestrial shots (use focal length + GPS) with
  easy manual correction in the review UI.
- Add a "raw conversion check" step that verifies all planned conversions exist and
  re-runs a whole group if any are missing.
- Make group JSON creation fully incremental (each step appends its section and
  saves a new version), enabling clean rollbacks.
- Add a flag to skip recipe-`0` RAW conversion when JPGs already exist.
- Consider merging ghost-map creation and HDR result blending into one step.
- Offer blended outputs across styles (e.g. 60% photographic / 40% adjusted) and
  sky-aware region-specific blending.

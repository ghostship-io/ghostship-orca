# orca-advanced-calibration

Local automation for **Ellis-style flow-ratio calibration** in
[OrcaSlicer](https://github.com/OrcaSlicer/OrcaSlicer). It generates a
ready-to-print plate of 30 × 30 × 3 mm test cubes, gives every cube its own
object-level `print_flow_ratio`, and sends the plate straight to a running
OrcaSlicer. Nothing leaves your machine, and Orca itself is never modified.

Two surfaces, one implementation:

- an **OrcaSlicer plugin** (`plugin.py`) that adds a docked panel to the
  slicer, and
- a **CLI** (`orca-calibrate`) for scripting and for Orca versions without the
  plugin API.

Both call the same pure-Python plate builder, so they always produce the same
file.

## Why

Orca's built-in flow calibration is a single-pass modifier sweep that runs from
the *Calibration* menu. Ellis' method is a different, print-and-inspect
workflow: a grid of small cubes, each printed at a known extrusion multiplier,
judged by the top surface. This tool packages that workflow so it is one
click instead of a manual plate arrangement.

## Install

Requires Python 3.12+. There are no runtime dependencies.

```sh
pip install .
```

### As an OrcaSlicer plugin

OrcaSlicer's [Python plugin system](https://github.com/OrcaSlicer/OrcaSlicer/wiki/plugins_getting_started)
is available in nightly builds and releases newer than 2.4.2. Install the
plugin file from **File → Plugins → Install local plugin**, then enable the
**Advanced Flow Calibration** capability.

A plugin is either a single self-contained `.py` file (PEP 723 metadata) or a
wheel. `scripts/build-plugin` produces the single-file bundle, which is the
simplest thing to install and to read.

## Use

CLI:

```sh
orca-calibrate presets
orca-calibrate plan --preset fine --filament-ratio 0.98
orca-calibrate generate --preset coarse --out plate.3mf
orca-calibrate generate --center 0.98 --step 0.005 --per-side 4 --send
```

`--send` hands the plate to the running OrcaSlicer; `--open` opens it with the
OS default handler instead.

Plugin: open **File → Plugins**, run **Advanced Flow Calibration**, and use the
docked panel. The panel prefills the centre with the active filament's flow
ratio.

## Parameters

| Parameter | Meaning |
| --- | --- |
| Center flow ratio | Extrusion multiplier at the centre of the range |
| Step | Flow-ratio increment between adjacent cubes |
| Samples per direction | Cubes on each side of centre; total is `2 × per_direction + 1` |

Presets:

| Preset | Step | Per side | Cubes | Range |
| --- | --- | --- | --- | --- |
| `coarse` | 0.020 | 3 | 7 | ± 0.060 |
| `fine` | 0.005 | 4 | 9 | ± 0.020 |
| `orca-yolo` | 0.010 | 5 | 11 | ± 0.050 |

The centre defaults to the active filament's flow ratio when it can be read.
Samples are always symmetric about the centre, and the plate is labelled and
arranged from lowest to highest flow.

## How the flow ratio is applied

Orca computes effective extrusion as

```
extrusion ∝ geometric volume × print_flow_ratio × filament_flow_ratio
```

(`GCode.cpp`, where both factors are multiplied into `_mm3_per_mm`).

This tool writes an **object-level `print_flow_ratio`** on each cube, normalised
against the active filament:

```
object override = (center + i × step) / filament_flow_ratio
```

so the number in each cube's name is the **effective** extrusion multiplier it
prints at, and the filament profile is left exactly as it was. See
[`docs/DESIGN.md`](docs/DESIGN.md) for the derivation and the alternatives.

## Handoff

Orca is single-instance. Starting its executable with the plate as a positional
argument forwards the path to the live instance and exits:

- **Windows** — `WM_COPYDATA` to the main window (matched by its instance hash).
- **macOS** — Apple event via `open -a`.
- **Linux** — DBus `AnotherInstance` message on the session bus.

`orca-calibrate doctor` reports what was found. `ORCASLICER_PATH` overrides
discovery.

## Layout

```
src/orca_advanced_calibration/
  params.py     presets and sample-plan math
  plate.py      grid layout and bed-fit checks
  three_mf.py   dependency-free Orca 3MF writer
  handoff.py    cross-platform send-to-Orca
  plugin.py     OrcaSlicer plugin entry point
  cli.py        orca-calibrate
docs/           method and design notes
tests/          unit and 3MF structural tests
```

## Development

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
python3 scripts/check-privacy.py
```

## Licence

MIT.

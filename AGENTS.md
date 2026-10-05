# Agent guide

A dependency-free Python tool that generates Ellis-style flow-ratio calibration
plates for OrcaSlicer and hands them to a running instance.

## Read before acting

- **How the plate is built and why**: `docs/DESIGN.md`. It records the 3MF
  format facts proven from the OrcaSlicer sources, the flow-ratio math, and the
  handoff strategies. Change a format assumption only with new evidence.
- **The calibration method**: `docs/ELLIS_METHOD.md`. It owns the print
  settings the generated plate assumes and the coarse → fine workflow.
- **The 3MF writer**: `src/orca_advanced_calibration/three_mf.py`. Its module
  docstring lists the reader constraints each decision satisfies.

## Rules

- No third-party runtime dependencies. The generator must work from a bare
  Python 3.12 install and from inside the OrcaSlicer plugin sandbox.
- The plate is local-only. Do not add network calls to plate generation or to
  handoff.
- Never modify the user's printer, filament or process presets. Per-cube values
  are object-level overrides, and the filament ratio is normalized against, not
  written to.
- Keep `filament_ratio` handling honest: a cube's name is the **effective**
  extrusion multiplier, which is `object_override * filament_ratio`.
- The plugin bundle (`orca-calibrate bundle`) must stay loadable with no
  filesystem writes at import time; plugin file creation is audited and would
  prompt the user.

## Checks

Run these before calling work done:

```sh
python -m pytest -q
ruff check .
ruff format --check .
```

To smoke-test the plugin bundle in isolation:

```sh
orca-calibrate bundle --out /tmp/plugin.py
orca-calibrate generate --preset fine --filament-ratio 0.98 --out /tmp/plate.3mf
```

Live slicing cannot be verified in a headless container: OrcaSlicer 2.4.2's GUI
segfaults before loading any file, including its own bundled 3MF. Structural
validation of the archive is the strongest automated check available here.

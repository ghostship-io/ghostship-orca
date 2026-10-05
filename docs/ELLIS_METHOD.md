# The Ellis method, as implemented

Source: [Ellis' Print Tuning Guide — Extrusion Multiplier](https://ellis3dp.com/Print-Tuning-Guide/articles/extrusion_multiplier.html).

This file owns the *method* the generated plate assumes. The mechanics of
building and sending the plate are in [`DESIGN.md`](DESIGN.md).

## Prerequisites

Ellis is explicit that extrusion multiplier is calibrated **last**, after:

1. extruder calibration (100 mm requested = 100 mm extruded), and
2. pressure advance.

The tool does not check this. If flow-ratio results look noisy, verify those
first.

## Why cubes

The method prints **30 × 30 × 3 mm** cubes. The judgment is purely visual and
tactile, on the **top surface**:

- **Too little** material leaves gaps, valleys, or shadows between the top
  extrusion lines.
- **Too much** material makes the top feel rough and look over-full.
- **Right** is a smooth, slightly shiny top whose lines overlap without
  bulging.

Two details matter for the plate to be judgeable:

- enough solid top layers that the multiplier difference compounds and becomes
  visible, and
- a couple of sparse layers below, so a squished first layer does not propagate
  up and read as over-extrusion.

The generated plate sets both; see the table in [`DESIGN.md`](DESIGN.md).

## The workflow the presets encode

| Stage | Preset | Step | Cubes | Range |
| --- | --- | --- | --- | --- |
| 1. Bracket | `coarse` | 0.020 | 7 | ± 0.060 |
| 2. Refine | `fine` | 0.005 | 9 | ± 0.020 |
| 3. Orca-compatible | `orca-yolo` | 0.010 | 11 | ± 0.050 |

Ellis describes a coarse pass "from 92% to 98%" — most filaments land there,
but not all — then a second pass at 0.5 % intervals. `coarse` covers a similar
span; `fine` is the second pass.

If the best cube is at an **edge** of the plate, the true optimum is outside
the range: re-center on the edge value and print again. Do not extrapolate from
a single result.

If two adjacent cubes are hard to separate, Ellis' guidance is to **pick the
higher** multiplier.

## Reading the plate

The plate is arranged from **lowest flow ratio to highest**, laid out in
reading order (left to right, then top to bottom), and every cube is named with
its absolute multiplier (`flow_0.9850`). Orca shows those names in the object
list.

Look at the **widest part of the top surface**, not the edges or corners, which
normally look slightly over-extruded even at the correct value. Tilting the cube
toward a light helps reveal gapping at low multipliers.

## Applying the result

The winning cube's name *is* the value to enter. Set the filament's **Flow
ratio** to that number in Orca and save the profile.

Because the plate normalizes against the filament ratio (see
[`DESIGN.md`](DESIGN.md)), printing a second plate after changing the filament
ratio still produces the same absolute range — the centre just tracks whatever
you saved.

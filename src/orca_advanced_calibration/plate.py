"""Plate planning: sample set -> arranged, printable geometry.

The plate is a single 3MF made of many independent objects (one per sample), so
each cube can carry its own ``print_flow_ratio`` object override. Objects are
laid out lowest-flow-first in reading order, which makes the physical plate
easy to scan against the labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .params import DEFAULT_PRESET, Preset, Sample, make_samples, preset, round_ratio

PLATE_VERSION = 1

# Ellis' guide: 30 x 30 x 3 mm cubes.
DEFAULT_CUBE_SIZE = (30.0, 30.0, 3.0)

# Bed clearance and inter-part spacing. Generous so a hand-arranged plate is
# never required and brims do not merge.
DEFAULT_MARGIN = 6.0
DEFAULT_GAP = 4.0

# The owner's QIDI X-Plus4 envelope (305 x 305 mm). Used only to report whether
# a plan fits; the generator never silently changes the user's parameters.
DEFAULT_BED = (305.0, 305.0)


@dataclass(frozen=True)
class PlatePlan:
    """A fully resolved, printable calibration plate."""

    samples: tuple[Sample, ...]
    center: float
    step: float
    side_count: int
    preset_name: str | None
    cube_size: tuple[float, float, float]
    columns: int
    rows: int
    bed: tuple[float, float]
    gap: float
    margin: float
    filament_ratio: float = 1.0
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def object_override(self, sample: Sample) -> float:
        """The object-level ``print_flow_ratio`` for ``sample``.

        Orca multiplies the geometric flow by ``print_flow_ratio`` *and* by the
        filament's ``filament_flow_ratio``. To make a cube print at the absolute
        multiplier in its label while leaving the filament profile untouched,
        the object override must be the target divided by the filament ratio.
        """
        return round_ratio(sample.flow_ratio / self.filament_ratio)

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def low(self) -> float:
        return self.samples[0].flow_ratio

    @property
    def high(self) -> float:
        return self.samples[-1].flow_ratio

    @property
    def required_bed(self) -> tuple[float, float]:
        width = 2 * self.margin + self.columns * self.cube_size[0] + (self.columns - 1) * self.gap
        depth = 2 * self.margin + self.rows * self.cube_size[1] + (self.rows - 1) * self.gap
        return (round(width, 3), round(depth, 3))

    @property
    def fits_bed(self) -> bool:
        width, depth = self.required_bed
        return width <= self.bed[0] and depth <= self.bed[1]

    def position(self, sample: Sample) -> tuple[float, float]:
        """Centre of ``sample``'s cube on the bed, in millimetres.

        Samples fill the grid in reading order (left-to-right, then top-to-
        bottom) starting from the lowest flow ratio. The grid is centred on the
        bed, so the plate is symmetric and orientation-independent.
        """
        col = sample.index % self.columns
        row = sample.index // self.columns

        cube_x, cube_y = self.cube_size[0], self.cube_size[1]
        grid_w = self.columns * cube_x + (self.columns - 1) * self.gap
        grid_h = self.rows * cube_y + (self.rows - 1) * self.gap

        origin_x = (self.bed[0] - grid_w) / 2.0
        origin_y = (self.bed[1] - grid_h) / 2.0

        return (
            round(origin_x + col * (cube_x + self.gap) + cube_x / 2.0, 4),
            round(origin_y + row * (cube_y + self.gap) + cube_y / 2.0, 4),
        )


def _grid_for(
    count: int,
    bed: tuple[float, float],
    cube: tuple[float, float, float],
    gap: float,
    margin: float,
) -> tuple[int, int]:
    """Pick the column count that makes the plate as square as possible.

    Ranking, in order: layouts that fit the bed, then the most square, then the
    fewest empty slots, then the most columns. The last two are tie-breakers so
    the choice is deterministic; squareness dominates because a long strip is
    hard to inspect and wastes bed width.
    """
    best: tuple[tuple, int, int] | None = None
    for columns in range(1, count + 1):
        rows = (count + columns - 1) // columns
        if columns * rows < count:
            continue
        w = 2 * margin + columns * cube[0] + (columns - 1) * gap
        h = 2 * margin + rows * cube[1] + (rows - 1) * gap
        fits = w <= bed[0] and h <= bed[1]
        wasted = columns * rows - count
        aspect = abs(w - h)
        score = (0 if fits else 1, round(aspect, 3), wasted, -columns)
        if best is None or score < best[0]:
            best = (score, columns, rows)
    if best is None:
        raise ValueError("could not lay out the requested samples")
    return best[1], best[2]


def build_plan(
    center_flow_ratio: float | None = None,
    step: float | None = None,
    side_count: int | None = None,
    *,
    preset_name: str | None = None,
    active_filament_ratio: float = 1.0,
    cube_size: tuple[float, float, float] = DEFAULT_CUBE_SIZE,
    bed: tuple[float, float] = DEFAULT_BED,
    gap: float = DEFAULT_GAP,
    margin: float = DEFAULT_MARGIN,
) -> PlatePlan:
    """Resolve user parameters into a validated :class:`PlatePlan`.

    Parameters
    ----------
    center_flow_ratio:
        Absolute extrusion multiplier at the centre of the range. ``None``
        falls back to the preset, then to ``active_filament_ratio`` (the
        currently selected filament's flow ratio), which is what the user
        almost always wants.
    step:
        Flow-ratio increment between adjacent cubes.
    side_count:
        Samples on each side of the centre; the total is ``2 * side_count + 1``.
    preset_name:
        Named starting point supplying whichever of the three values were not
        given explicitly.
    active_filament_ratio:
        The running filament profile's flow ratio. Used as the default centre.
    """
    selected: Preset | None = None
    if preset_name is not None:
        selected = preset(preset_name)
    elif center_flow_ratio is None or step is None or side_count is None:
        selected = preset(DEFAULT_PRESET)

    if selected is not None:
        if step is None:
            step = selected.step
        if side_count is None:
            side_count = selected.side_count
        if center_flow_ratio is None and selected.center_flow_ratio is not None:
            center_flow_ratio = selected.center_flow_ratio

    if active_filament_ratio <= 0:
        raise ValueError("active_filament_ratio must be positive")
    # The preset already supplied step/side_count; these fallbacks only apply
    # when the caller passed a preset that leaves a field unset (never today,
    # but keeps the contract explicit).
    if center_flow_ratio is None:
        center_flow_ratio = active_filament_ratio
    if step is None:
        raise ValueError("step is required when no preset supplies one")
    if side_count is None:
        raise ValueError("side_count is required when no preset supplies one")

    samples = make_samples(center_flow_ratio, step, side_count)
    columns, rows = _grid_for(len(samples), bed, cube_size, gap, margin)

    plan = PlatePlan(
        samples=tuple(samples),
        center=center_flow_ratio,
        step=step,
        side_count=side_count,
        preset_name=selected.name if selected else None,
        cube_size=cube_size,
        columns=columns,
        rows=rows,
        bed=bed,
        gap=gap,
        margin=margin,
        filament_ratio=active_filament_ratio,
    )

    warnings: list[str] = []
    if not plan.fits_bed:
        w, d = plan.required_bed
        warnings.append(
            f"plate is {w} x {d} mm and exceeds the {bed[0]} x {bed[1]} mm bed; "
            "reduce side_count or step"
        )
    return PlatePlan(**{**plan.__dict__, "warnings": tuple(warnings)})

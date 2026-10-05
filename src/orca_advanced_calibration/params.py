"""Parameter model, presets, and sample-plan math for the calibration plate."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Ellis' guide recommends 1-2% EM steps for the coarse pass and 0.5% for the
# fine pass, on 30 x 30 x 3 mm cubes. See docs/ELLIS_METHOD.md.
MIN_STEP = 0.001
MAX_STEP = 0.10
MIN_SIDE_COUNT = 1
MAX_SIDE_COUNT = 12
MIN_RATIO = 0.50
MAX_RATIO = 1.50

# Bounds on the resulting cube count. A plate that does not fit the bed is a
# contract failure, not something to silently shrink.
MAX_SAMPLES = 41

# Orca profiles carry flow ratios at 4 dp; matches that precision.
RATIO_DECIMALS = 4


def total_samples(side_count: int) -> int:
    """Total cubes for a symmetric plan: ``2 * side_count + 1``."""
    return 2 * side_count + 1


@dataclass(frozen=True)
class Preset:
    """A named starting point for the three primary parameters."""

    name: str
    center_flow_ratio: float | None
    step: float
    side_count: int
    description: str

    @property
    def samples(self) -> int:
        return total_samples(self.side_count)

    @property
    def span(self) -> float:
        """Half-width of the calibrated range, as a flow-ratio delta."""
        return self.step * self.side_count


PRESETS: dict[str, Preset] = {
    "coarse": Preset(
        name="coarse",
        center_flow_ratio=None,
        step=0.02,
        side_count=3,
        description="Wide first pass (7 cubes, +/-0.06) to bracket the right range.",
    ),
    "fine": Preset(
        name="fine",
        center_flow_ratio=None,
        step=0.005,
        side_count=4,
        description="Narrow second pass (9 cubes, +/-0.02) once coarse is known.",
    ),
    "orca-yolo": Preset(
        name="orca-yolo",
        center_flow_ratio=None,
        step=0.01,
        side_count=5,
        description="Matches OrcaSlicer's built-in YOLO pass (11 cubes, +/-0.05).",
    ),
}

DEFAULT_PRESET = "coarse"


def preset(name: str) -> Preset:
    """Look up a preset by name (case-insensitive)."""
    try:
        return PRESETS[name.strip().lower()]
    except KeyError:
        known = ", ".join(sorted(PRESETS))
        raise KeyError(f"unknown preset {name!r}; known presets: {known}") from None


def round_ratio(value: float) -> float:
    # ``+ 0.0`` normalises -0.0 to 0.0 so labels and sorting stay stable.
    return round(value + 0.0, RATIO_DECIMALS)


@dataclass(frozen=True)
class Sample:
    """One calibration cube and the absolute flow ratio it demonstrates."""

    index: int
    flow_ratio: float
    center: float

    @property
    def offset(self) -> float:
        """Signed distance from the plan centre, in flow-ratio units."""
        return round_ratio(self.flow_ratio - self.center)

    @property
    def label(self) -> str:
        """Object name carrying the absolute flow ratio, e.g. ``flow_0.9800``."""
        return label_for(self.flow_ratio)


def label_for(ratio: float) -> str:
    """Human-readable object name carrying the absolute flow ratio.

    The name is what Orca shows in the object list, so it must encode the value
    unambiguously.
    """
    return f"flow_{round_ratio(ratio):.4f}"


def sample_ratios(center: float, step: float, side_count: int) -> list[float]:
    """Absolute flow ratios from lowest to highest, symmetric about ``center``.

    The centre sample is included exactly once, so the result has
    ``2 * side_count + 1`` entries. Values are de-duplicated after rounding,
    which keeps a very small step from emitting identical cubes.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    if side_count < 0:
        raise ValueError("side_count must not be negative")

    raw = [center + (offset * step) for offset in range(-side_count, side_count + 1)]
    seen = {round_ratio(value): None for value in raw}
    return sorted(seen)


def _validate(center: float, step: float, side_count: int) -> None:
    if not (MIN_RATIO <= center <= MAX_RATIO):
        raise ValueError(
            f"center flow ratio {center} outside supported range [{MIN_RATIO}, {MAX_RATIO}]"
        )
    if not (MIN_STEP <= step <= MAX_STEP):
        raise ValueError(f"step {step} outside supported range [{MIN_STEP}, {MAX_STEP}]")
    if not (MIN_SIDE_COUNT <= side_count <= MAX_SIDE_COUNT):
        raise ValueError(
            f"side_count {side_count} outside supported range [{MIN_SIDE_COUNT}, {MAX_SIDE_COUNT}]"
        )
    if total_samples(side_count) > MAX_SAMPLES:
        raise ValueError(
            f"{total_samples(side_count)} samples exceeds the {MAX_SAMPLES}-cube plate limit"
        )


def make_samples(center: float, step: float, side_count: int) -> list[Sample]:
    _validate(center, step, side_count)
    ratios = sample_ratios(center, step, side_count)
    return [Sample(index=i, flow_ratio=r, center=center) for i, r in enumerate(ratios)]


def iter_labels(samples: Iterable[Sample]) -> list[str]:
    return [s.label for s in samples]

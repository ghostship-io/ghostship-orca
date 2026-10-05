"""Automated Ellis-style flow-ratio calibration plate generator for OrcaSlicer.

The package builds a ready-to-print plate of 30 x 30 x 3 mm Ellis test cubes,
gives every cube its own object-level ``print_flow_ratio`` override, packages
the result as an Orca-compatible 3MF, and hands it to a running OrcaSlicer.

Public entry points:

* :func:`build_plan` - turn user parameters into a validated sample plan.
* :func:`write_plate` - render a plan to a 3MF byte string / file.
* :func:`handoff` - send a generated file to a running OrcaSlicer.
"""

from .params import PRESETS, preset
from .plate import PLATE_VERSION, PlatePlan, build_plan
from .three_mf import write_plate, write_plate_bytes

__all__ = [
    "PLATE_VERSION",
    "PRESETS",
    "PlatePlan",
    "build_plan",
    "preset",
    "write_plate",
    "write_plate_bytes",
    "handoff",
]


def __getattr__(name: str):
    # Imported lazily so that importing the package (and the pure-stdlib 3MF
    # writer) never pulls in platform handoff code. ``importlib`` is used
    # rather than ``from . import`` to avoid re-entering this hook.
    if name == "handoff":
        import importlib

        module = importlib.import_module(f"{__name__}.handoff")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Command-line interface: the same plates the plugin produces, scriptable.

Usage::

    orca-calibrate generate --preset coarse --out plate.3mf
    orca-calibrate generate --center 0.98 --step 0.005 --per-side 4 --send
    orca-calibrate presets
    orca-calibrate doctor
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import PRESETS, build_plan, write_plate
from .handoff import describe_environment, find_orca_slicer, handoff, open_path
from .params import Preset
from .plate import DEFAULT_BED, DEFAULT_CUBE_SIZE


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        default=None,
        help="named starting point for step/samples (default: coarse)",
    )
    parser.add_argument(
        "--center",
        type=float,
        default=None,
        help="center flow ratio; defaults to the active filament's flow ratio "
        "when it can be read, otherwise 1.0",
    )
    parser.add_argument("--step", type=float, default=None, help="flow-ratio increment")
    parser.add_argument(
        "--per-side",
        type=int,
        default=None,
        help="samples on each side of center; total = 2*per_side + 1",
    )
    parser.add_argument(
        "--filament-ratio",
        type=float,
        default=1.0,
        help="flow ratio of the filament that will print the plate; the center "
        "defaults to this value",
    )
    parser.add_argument(
        "--bed",
        type=float,
        nargs=2,
        metavar=("W", "D"),
        default=list(DEFAULT_BED),
        help="bed size in mm (default: 305 305)",
    )
    parser.add_argument(
        "--cube",
        type=float,
        nargs=3,
        metavar=("X", "Y", "Z"),
        default=list(DEFAULT_CUBE_SIZE),
        help="test cube size in mm (default: 30 30 3)",
    )


def _build(args) -> object:
    return build_plan(
        center_flow_ratio=args.center,
        step=args.step,
        side_count=args.per_side,
        preset_name=args.preset,
        active_filament_ratio=args.filament_ratio,
        cube_size=tuple(args.cube),
        bed=tuple(args.bed),
    )


def _default_name(plan) -> str:
    tag = f"{plan.center:.4f}".replace(".", "p")
    return f"flow-calibration-{tag}-{plan.step:.4f}-{plan.count}cubes.3mf"


def cmd_generate(args) -> int:
    plan = _build(args)
    out = args.out or os.path.join(os.getcwd(), _default_name(plan))
    write_plate(plan, out)

    print(f"Wrote {out}")
    print(
        f"{plan.count} cubes: {plan.low:.4f} .. {plan.high:.4f} "
        f"(step {plan.step:g}, center {plan.center:.4f})"
    )
    width, depth = plan.required_bed
    print(f"Grid {plan.columns} x {plan.rows}; needs {width} x {depth} mm")
    for warning in plan.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if args.send:
        try:
            print(handoff(out))
        except FileNotFoundError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    elif args.open:
        print(open_path(out))
    return 0


def cmd_list(args) -> int:
    for name in sorted(PRESETS):
        p: Preset = PRESETS[name]
        print(
            f"{name:10s} step {p.step:<6g} per-side {p.side_count:<3d} "
            f"cubes {p.samples:<3d} span +/-{p.span:g}"
        )
        print(f"           {p.description}")
    return 0


def cmd_doctor(args) -> int:
    info = describe_environment()
    for key, value in info.items():
        print(f"{key:12s} {value}")
    if find_orca_slicer() is None:
        print(
            "\nNo OrcaSlicer executable found. Set ORCASLICER_PATH to its path to enable --send.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_plan(args) -> int:
    plan = _build(args)
    payload = {
        "center": plan.center,
        "step": plan.step,
        "side_count": plan.side_count,
        "count": plan.count,
        "low": plan.low,
        "high": plan.high,
        "columns": plan.columns,
        "rows": plan.rows,
        "required_bed": list(plan.required_bed),
        "warnings": list(plan.warnings),
        "samples": [
            {"label": s.label, "flow_ratio": s.flow_ratio, "position": list(plan.position(s))}
            for s in plan.samples
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_bundle(args) -> int:
    from . import bundle as bundler

    if args.info:
        print(json.dumps(bundler.bundle_summary(), indent=2))
        return 0

    out = args.out or os.path.join(os.getcwd(), bundler.default_bundle_name())
    bundler.write_bundle(out)
    print(f"Wrote {out}")
    print("Install it from File -> Plugins -> Install local plugin, then enable it.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orca-calibrate",
        description="Generate Ellis-style flow-ratio calibration plates for OrcaSlicer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="write a calibration plate 3MF")
    _add_common(gen)
    gen.add_argument("--out", "-o", default=None, help="output .3mf path")
    gen.add_argument(
        "--send",
        action="store_true",
        help="send the plate to the running OrcaSlicer instance",
    )
    gen.add_argument(
        "--open",
        action="store_true",
        help="open the plate with the OS default handler instead of sending to Orca",
    )
    gen.set_defaults(func=cmd_generate)

    ls = sub.add_parser("presets", help="list the named presets")
    ls.set_defaults(func=cmd_list)

    doc = sub.add_parser("doctor", help="report the OrcaSlicer environment")
    doc.set_defaults(func=cmd_doctor)

    bundle = sub.add_parser(
        "bundle",
        help="build a self-contained single-file OrcaSlicer plugin",
    )
    bundle.add_argument("--out", "-o", default=None, help="output .py path")
    bundle.add_argument(
        "--info", action="store_true", help="print bundle metadata instead of writing a file"
    )
    bundle.set_defaults(func=cmd_bundle)

    pl = sub.add_parser("plan", help="print the resolved plan as JSON; writes nothing")
    _add_common(pl)
    pl.set_defaults(func=cmd_plan)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

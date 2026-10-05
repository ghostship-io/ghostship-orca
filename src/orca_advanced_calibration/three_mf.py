"""Minimal, self-contained Orca-compatible 3MF writer.

OrcaSlicer loads ``.3mf`` files through ``load_bbs_3mf`` (``Model.cpp:520``) unless
the file carries a ``PrusaSlicer`` Application tag. All object-level settings --
including the per-cube ``print_flow_ratio`` we depend on -- live in
``Metadata/model_settings.config``, which only the Bambu/Orca reader parses
(``bbs_3mf.cpp``, ``BBS_MODEL_CONFIG_FILE``). This module writes that dialect
directly, with no external dependency, so the plate is generated entirely
locally.

Structure produced::

    [Content_Types].xml
    _rels/.rels
    3D/3dmodel.model                 # geometry + <build> placements
    Metadata/model_settings.config  # per-object name + print_flow_ratio
    Metadata/project_settings.config  # sparse JSON of process/filament overrides

Design constraints proven from the Orca sources:

* ``3dmodel.model`` must NOT declare ``<metadata name="Application">BambuStudio|OrcaSlicer``
  *and* be Prusa-style, or the wrong importer is used. We declare the Orca
  Application tag so ``load_bbs_3mf`` handles object overrides.
* A build item's transform is a 4x3 **column-major** matrix (``r`` varies
  fastest), per ``bbs_get_transform_from_3mf_specs_string``.
* ``model_settings.config`` object ``id`` attributes must match the resource
  object ids in ``3dmodel.model``; the reader fails on duplicates
  (``_handle_start_config_object``).
"""

from __future__ import annotations

import io
import json
import zipfile
from xml.sax.saxutils import escape, quoteattr

from .plate import PlatePlan

ORCA_VERSION = "2.4.2"
# The reader treats an ``OrcaSlicer`` metadata tag as authoritative for
# version detection (``_handle_end_metadata``); the Application tag must still
# carry the Orca prefix so it is classified as an Orca/Bambu 3MF.
APPLICATION_TAG = f"OrcaSlicer-{ORCA_VERSION}"

# 3MF core unit is millimetre.
MODEL_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
BAMBU_NS = "http://schemas.bambulab.com/package/2021"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>"""

# Identical in spirit to _add_relationships_file_to_archive: a single rel to the
# model part. Deliberately no 3D/_rels/3dmodel.model.rels, whose presence makes
# PrusaFileParser look for a Prusa Application tag.
ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""


def _fmt(value: float) -> str:
    """Format a coordinate compactly but without losing meaningful precision."""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _box_triangles(
    size: tuple[float, float, float],
) -> tuple[list[tuple[float, float, float]], list[tuple[int, int, int]]]:
    """Vertices and triangles for a box centred on XY and seated at Z=0."""
    sx, sy, sz = (s / 2.0 for s in size)
    vertices = [
        (-sx, -sy, 0.0),
        (sx, -sy, 0.0),
        (sx, sy, 0.0),
        (-sx, sy, 0.0),
        (-sx, -sy, sz),
        (sx, -sy, sz),
        (sx, sy, sz),
        (-sx, sy, sz),
    ]
    # Winding is CCW as seen from outside, so the mesh is a valid closed solid.
    triangles = [
        (0, 2, 1),
        (0, 3, 2),  # bottom
        (4, 5, 6),
        (4, 6, 7),  # top
        (0, 1, 5),
        (0, 5, 4),  # -Y
        (1, 2, 6),
        (1, 6, 5),  # +X
        (2, 3, 7),
        (2, 7, 6),  # +Y
        (3, 0, 4),
        (3, 4, 7),  # -X
    ]
    return vertices, triangles


def _model_xml(plan: PlatePlan) -> str:
    vertices, triangles = _box_triangles(plan.cube_size)

    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write(
        f'<model unit="millimeter" xml:lang="en-US" '
        f'xmlns="{MODEL_NS}" xmlns:BambuStudio="{BAMBU_NS}">\n'
    )
    out.write(f' <metadata name="Application">{escape(APPLICATION_TAG)}</metadata>\n')
    out.write(f' <metadata name="OrcaSlicer">{ORCA_VERSION}</metadata>\n')
    out.write(' <metadata name="BambuStudio:3mfVersion">1</metadata>\n')
    out.write(' <metadata name="Title">Flow ratio calibration plate</metadata>\n')
    out.write(" <resources>\n")

    for sample in plan.samples:
        object_id = sample.index + 1
        out.write(f'  <object id="{object_id}" type="model">\n')
        out.write("   <mesh>\n")
        out.write("    <vertices>\n")
        for x, y, z in vertices:
            out.write(f'     <vertex x="{_fmt(x)}" y="{_fmt(y)}" z="{_fmt(z)}"/>\n')
        out.write("    </vertices>\n")
        out.write("    <triangles>\n")
        for a, b, c in triangles:
            out.write(f'     <triangle v1="{a}" v2="{b}" v3="{c}"/>\n')
        out.write("    </triangles>\n")
        out.write("   </mesh>\n")
        out.write("  </object>\n")

    out.write(" </resources>\n")
    out.write(" <build>\n")
    for sample in plan.samples:
        object_id = sample.index + 1
        cx, cy = plan.position(sample)
        out.write(
            f'  <item objectid="{object_id}" '
            f'transform="1 0 0 0 1 0 0 0 1 {_fmt(cx)} {_fmt(cy)} 0" '
            'printable="1" auto_drop="1"/>\n'
        )
    out.write(" </build>\n")
    out.write("</model>\n")
    return out.getvalue()


def _model_settings_xml(plan: PlatePlan) -> str:
    """Per-object overrides: the object name and its ``print_flow_ratio``.

    ``print_flow_ratio`` is an object-scope option (``PrintObjectConfig``), so a
    value here multiplies the filament ratio just like Orca's own calibration
    does (``GCode.cpp``: effective = geometric * print_flow_ratio * filament).
    :meth:`PlatePlan.object_override` divides the target multiplier by the
    filament ratio, so the label is the effective multiplier.
    """
    out = io.StringIO()
    out.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    out.write("<config>\n")

    for sample in plan.samples:
        object_id = sample.index + 1
        out.write(f'  <object id="{object_id}">\n')
        out.write(f'    <metadata key="name" value={quoteattr(sample.label)}/>\n')
        # The object-level override that makes this cube a distinct sample.
        # Normalised against the filament ratio so the label equals the
        # effective extrusion multiplier. Written in Orca profile notation.
        override = plan.object_override(sample)
        out.write(f'    <metadata key="print_flow_ratio" value="{override:.4f}"/>\n')
        # The part id must equal the mesh resource id, because the loader
        # matches volumes by ``subobject_id`` (_generate_volumes_new).
        out.write(f'    <part id="{object_id}" subtype="normal_part">\n')
        out.write(f'      <metadata key="name" value={quoteattr(sample.label)}/>\n')
        out.write("    </part>\n")
        out.write("  </object>\n")

    out.write("  <plate>\n")
    out.write('    <metadata key="plater_id" value="1"/>\n')
    out.write('    <metadata key="plater_name" value=""/>\n')
    out.write('    <metadata key="locked" value="false"/>\n')
    for sample in plan.samples:
        object_id = sample.index + 1
        out.write("    <model_instance>\n")
        out.write(f'      <metadata key="object_id" value="{object_id}"/>\n')
        out.write('      <metadata key="instance_id" value="0"/>\n')
        out.write(f'      <metadata key="identify_id" value="{object_id}"/>\n')
        out.write("    </model_instance>\n")
    out.write("  </plate>\n")
    out.write("</config>\n")
    return out.getvalue()


def _process_overrides(plan: PlatePlan, overrides: dict[str, str | int | float] | None) -> dict:
    """Ellis process settings, plus the caller's overrides.

    Ellis specifies enough solid top layers to expose the extrusion multiplier
    difference, sparse bottom layers so first-layer squish does not propagate,
    and ironing off. These are the process-level knobs; the per-cube flow ratio
    stays an object override.
    """
    settings: dict[str, str] = {
        "top_surface_pattern": "monotonic",
        "top_shell_layers": "6",
        "bottom_shell_layers": "2",
        "sparse_infill_density": "35%",
        "sparse_infill_pattern": "rectilinear",
        "ironing_type": "no ironing",
        "wall_loops": "2",
    }
    if overrides:
        for key, value in overrides.items():
            if isinstance(value, bool):
                settings[key] = "1" if value else "0"
            else:
                settings[key] = str(value)
    return settings


def write_plate_bytes(
    plan: PlatePlan,
    *,
    process_overrides: dict[str, str | int | float] | None = None,
) -> bytes:
    """Render ``plan`` to an Orca-compatible 3MF archive, in memory."""
    if not plan.samples:
        raise ValueError("plan has no samples to write")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", ROOT_RELS)
        archive.writestr("3D/3dmodel.model", _model_xml(plan))
        archive.writestr("Metadata/model_settings.config", _model_settings_xml(plan))
        archive.writestr(
            "Metadata/project_settings.config",
            json.dumps(_process_overrides(plan, process_overrides), indent=4) + "\n",
        )
    return buffer.getvalue()


def write_plate(
    plan: PlatePlan,
    path: str,
    *,
    process_overrides: dict[str, str | int | float] | None = None,
) -> str:
    """Write ``plan`` as a 3MF at ``path``; returns the path as a string."""
    data = write_plate_bytes(plan, process_overrides=process_overrides)
    with open(path, "wb") as handle:
        handle.write(data)
    return str(path)

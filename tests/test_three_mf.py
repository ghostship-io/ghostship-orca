"""Tests for plate layout, bed fitting, and the 3MF writer."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from orca_advanced_calibration import build_plan, write_plate, write_plate_bytes

MODEL_NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def _plan(**kwargs):
    kwargs.setdefault("preset_name", "coarse")
    kwargs.setdefault("active_filament_ratio", 1.0)
    return build_plan(**kwargs)


class TestLayout:
    def test_total_matches_formula(self):
        for side in (1, 3, 5, 8):
            plan = build_plan(center_flow_ratio=1.0, step=0.01, side_count=side)
            assert plan.count == 2 * side + 1

    def test_grid_covers_every_sample(self):
        plan = _plan()
        assert plan.columns * plan.rows >= plan.count

    def test_grid_prefers_square_over_strip(self):
        # 7 cubes must not be laid out as a 1 x 7 strip.
        plan = _plan()
        assert min(plan.columns, plan.rows) >= 2

    def test_positions_are_distinct(self):
        plan = _plan()
        positions = [plan.position(s) for s in plan.samples]
        assert len(set(positions)) == len(positions)

    def test_ordering_follows_flow_low_to_high(self):
        # Reading order across the grid is lowest-flow first.
        plan = build_plan(center_flow_ratio=1.0, step=0.01, side_count=5)
        by_position = sorted(
            plan.samples, key=lambda s: (s.index // plan.columns, s.index % plan.columns)
        )
        assert [s.flow_ratio for s in by_position] == sorted(s.flow_ratio for s in by_position)

    def test_grid_block_is_centered_on_bed(self):
        # The full columns x rows block is centred, so the plate is symmetric
        # regardless of how many trailing cells are empty.
        plan = _plan()
        xs = [plan.position(s)[0] for s in plan.samples]
        half_cube = plan.cube_size[0] / 2
        grid_w = plan.columns * plan.cube_size[0] + (plan.columns - 1) * plan.gap
        assert min(xs) - half_cube == pytest.approx((plan.bed[0] - grid_w) / 2, abs=1e-6)
        assert max(xs) + half_cube == pytest.approx((plan.bed[0] + grid_w) / 2, abs=1e-6)

    def test_fits_owner_bed(self):
        plan = _plan()
        assert plan.fits_bed

    def test_default_center_comes_from_filament(self):
        plan = build_plan(preset_name="fine", active_filament_ratio=0.97)
        assert plan.center == pytest.approx(0.97)

    def test_explicit_center_wins_over_filament(self):
        plan = build_plan(center_flow_ratio=1.02, preset_name="fine", active_filament_ratio=0.97)
        assert plan.center == pytest.approx(1.02)

    def test_requiring_preset_when_all_params_given(self):
        plan = build_plan(center_flow_ratio=1.0, step=0.02, side_count=2)
        assert plan.preset_name is None
        assert plan.count == 5

    def test_oversized_plate_warns_but_still_builds(self):
        plan = build_plan(center_flow_ratio=1.0, step=0.02, side_count=6, bed=(80.0, 80.0))
        assert not plan.fits_bed
        assert plan.warnings

    def test_rejects_zero_filament_ratio(self):
        with pytest.raises(ValueError):
            build_plan(preset_name="coarse", active_filament_ratio=0.0)


class TestThreeMF:
    @pytest.fixture()
    def archive(self):
        return zipfile.ZipFile(io.BytesIO(write_plate_bytes(_plan())))

    def test_required_parts_present(self, archive):
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= names
        assert "Metadata/model_settings.config" in names
        assert "Metadata/project_settings.config" in names

    def test_model_is_well_formed_xml(self, archive):
        ET.fromstring(archive.read("3D/3dmodel.model"))

    def test_model_settings_is_well_formed_xml(self, archive):
        ET.fromstring(archive.read("Metadata/model_settings.config"))

    def test_project_settings_is_json(self, archive):
        payload = json.loads(archive.read("Metadata/project_settings.config"))
        assert isinstance(payload, dict)
        assert payload["ironing_type"] == "no ironing"
        assert payload["top_surface_pattern"] == "monotonic"

    def test_application_tag_routes_to_orca_importer(self, archive):
        # A PrusaSlicer Application tag would send the file to the Prusa reader,
        # which ignores object overrides. Ours must stay Orca-branded.
        model = archive.read("3D/3dmodel.model").decode()
        assert "OrcaSlicer-" in model
        assert "PrusaSlicer" not in model
        # No 3D/_rels/3dmodel.model.rels: its presence makes PrusaFileParser
        # inspect the Application tag for PrusaSlicer.
        assert "3D/_rels/3dmodel.model.rels" not in archive.namelist()

    def test_object_count_matches_plan(self, archive):
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
        objects = root.findall(f"{MODEL_NS}resources/{MODEL_NS}object")
        assert len(objects) == _plan().count

    def test_every_object_is_placed_in_build(self, archive):
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
        object_ids = {o.get("id") for o in root.findall(f"{MODEL_NS}resources/{MODEL_NS}object")}
        build_ids = {
            item.get("objectid") for item in root.findall(f"{MODEL_NS}build/{MODEL_NS}item")
        }
        assert build_ids == object_ids

    def test_build_transform_is_4x3(self, archive):
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
        for item in root.findall(f"{MODEL_NS}build/{MODEL_NS}item"):
            values = item.get("transform").split()
            assert len(values) == 12
            # Identity rotation with a Z=0 translation.
            assert values[:9] == ["1", "0", "0", "0", "1", "0", "0", "0", "1"]
            assert values[11] == "0"

    def test_print_flow_overrides_present_and_unique(self, archive):
        root = ET.fromstring(archive.read("Metadata/model_settings.config"))
        ratios = []
        for obj in root.findall("object"):
            entries = {m.get("key"): m.get("value") for m in obj.findall("metadata")}
            assert "print_flow_ratio" in entries
            ratios.append(float(entries["print_flow_ratio"]))
        assert len(ratios) == len(set(ratios))

    def test_object_names_match_labels(self, archive):
        root = ET.fromstring(archive.read("Metadata/model_settings.config"))
        object_names = [
            m.get("value")
            for obj in root.findall("object")
            for m in obj.findall("metadata")
            if m.get("key") == "name"
        ]
        part_names = [
            m.get("value")
            for part in root.findall("object/part")
            for m in part.findall("metadata")
            if m.get("key") == "name"
        ]
        expected = [s.label for s in _plan().samples]
        assert object_names == expected
        assert part_names == expected

    def test_override_ids_match_model_resource_ids(self, archive):
        model = ET.fromstring(archive.read("3D/3dmodel.model"))
        settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
        model_ids = {o.get("id") for o in model.findall(f"{MODEL_NS}resources/{MODEL_NS}object")}
        settings_ids = {obj.get("id") for obj in settings.findall("object")}
        assert model_ids == settings_ids

    def test_part_id_matches_its_object_id(self, archive):
        settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
        for obj in settings.findall("object"):
            part = obj.find("part")
            assert part is not None
            assert part.get("id") == obj.get("id")

    def test_no_duplicate_object_ids(self, archive):
        settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
        ids = [obj.get("id") for obj in settings.findall("object")]
        assert len(ids) == len(set(ids))

    def test_plate_block_lists_every_instance(self, archive):
        settings = ET.fromstring(archive.read("Metadata/model_settings.config"))
        plate = settings.find("plate")
        assert plate is not None
        instances = plate.findall("model_instance")
        assert len(instances) == _plan().count
        listed = {
            m.get("value")
            for inst in instances
            for m in inst.findall("metadata")
            if m.get("key") == "object_id"
        }
        assert len(listed) == _plan().count

    def test_mesh_triangles_index_valid_vertices(self, archive):
        root = ET.fromstring(archive.read("3D/3dmodel.model"))
        for obj in root.findall(f"{MODEL_NS}resources/{MODEL_NS}object"):
            vertex_count = len(obj.findall(f"{MODEL_NS}mesh/{MODEL_NS}vertices/{MODEL_NS}vertex"))
            for tri in obj.findall(f"{MODEL_NS}mesh/{MODEL_NS}triangles/{MODEL_NS}triangle"):
                for attr in ("v1", "v2", "v3"):
                    assert 0 <= int(tri.get(attr)) < vertex_count


class TestWritePlate:
    def test_writes_file(self, tmp_path):
        target = tmp_path / "plate.3mf"
        returned = write_plate(_plan(), str(target))
        assert returned == str(target)
        assert target.exists()
        assert zipfile.ZipFile(target).testzip() is None

    def test_rejects_empty_plan(self, tmp_path):
        plan = _plan()
        empty = plan.__class__(**{**plan.__dict__, "samples": ()})
        with pytest.raises(ValueError):
            write_plate_bytes(empty)


class TestOverrideSemantics:
    """The label must equal the effective extrusion multiplier.

    Orca applies ``geometric * print_flow_ratio * filament_flow_ratio``
    (GCode.cpp). The writer therefore stores ``target / filament_ratio``, so
    that ``stored_override * filament_ratio == label`` for every cube.
    """

    @staticmethod
    def _stored_overrides(plan):
        archive = zipfile.ZipFile(io.BytesIO(write_plate_bytes(plan)))
        root = ET.fromstring(archive.read("Metadata/model_settings.config"))
        result = {}
        for obj in root.findall("object"):
            entries = {m.get("key"): m.get("value") for m in obj.findall("metadata")}
            result[entries["name"]] = float(entries["print_flow_ratio"])
        return result

    def test_stored_override_times_filament_equals_label(self):
        filament = 0.97
        plan = build_plan(
            center_flow_ratio=0.97, step=0.02, side_count=3, active_filament_ratio=filament
        )
        stored = self._stored_overrides(plan)
        assert len(stored) == plan.count
        for sample in plan.samples:
            effective = stored[sample.label] * filament
            assert effective == pytest.approx(sample.flow_ratio, abs=5e-5)

    def test_center_override_is_one_when_center_is_filament(self):
        plan = build_plan(preset_name="fine", active_filament_ratio=0.97)
        stored = self._stored_overrides(plan)
        center_label = next(s.label for s in plan.samples if s.flow_ratio == plan.center)
        assert stored[center_label] == pytest.approx(1.0, abs=1e-6)

    def test_filament_one_leaves_label_as_override(self):
        plan = build_plan(center_flow_ratio=1.0, step=0.02, side_count=2, active_filament_ratio=1.0)
        stored = self._stored_overrides(plan)
        for sample in plan.samples:
            assert stored[sample.label] == pytest.approx(sample.flow_ratio)


class TestDesignInvariants:
    """The load-bearing invariants from docs/DESIGN.md."""

    def test_effective_multiplier_equals_label_for_every_filament(self):
        for filament in (0.85, 0.92, 0.98, 1.0, 1.12):
            plan = build_plan(
                center_flow_ratio=filament,
                step=0.005,
                side_count=4,
                active_filament_ratio=filament,
            )
            for sample in plan.samples:
                effective = plan.object_override(sample) * filament
                assert effective == pytest.approx(sample.flow_ratio, abs=5e-5), (
                    filament,
                    sample.label,
                )

    def test_range_is_symmetric_about_the_center(self):
        plan = build_plan(preset_name="fine", active_filament_ratio=0.98)
        assert plan.center - plan.low == pytest.approx(plan.high - plan.center)

    def test_center_override_makes_the_center_cube_match_the_filament(self):
        plan = build_plan(preset_name="fine", active_filament_ratio=0.98)
        center = next(s for s in plan.samples if s.flow_ratio == plan.center)
        # Override 1.0 means the centre cube prints at exactly the filament ratio.
        assert plan.object_override(center) == pytest.approx(1.0, abs=1e-6)

    def test_plate_stays_within_the_default_bed_for_all_presets(self):
        from orca_advanced_calibration.params import PRESETS

        for name in PRESETS:
            plan = build_plan(preset_name=name, active_filament_ratio=0.98)
            assert plan.fits_bed, name
            assert not plan.warnings, name

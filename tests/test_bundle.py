"""Tests for the single-file plugin bundler.

The bundle must be (a) a valid PEP 723 + Orca plugin header, (b) importable as a
synthetic package with working relative imports, and (c) equivalent to the
installed package. These tests execute it against a stub of the ``orca`` host.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import types
import xml.etree.ElementTree as ET
import zipfile

import pytest

from orca_advanced_calibration import bundle
from orca_advanced_calibration.params import PRESETS


class _StubOrca(types.ModuleType):
    """Enough of the Orca plugin API to load the bundle and call it."""

    def __init__(self):
        super().__init__("orca")
        self.registered = []
        self.executions = []

        class ExecutionResult:
            @staticmethod
            def success(message=""):
                return ("success", message)

        class ScriptPluginCapabilityBase:
            def get_name(self):
                return ""

        class Base:
            def register_capabilities(self):
                pass

        outer = self

        class Plugin:
            def __init__(self, cls):
                outer.registered.append(cls)
                cls().register_capabilities()

        self.ExecutionResult = ExecutionResult
        self.script = types.SimpleNamespace(ScriptPluginCapabilityBase=ScriptPluginCapabilityBase)
        self.base = Base
        self.plugin = Plugin
        self.register_capability = self.registered.append
        self.host = types.SimpleNamespace(
            ui=types.SimpleNamespace(create_dock_panel=lambda **kw: None),
            preset_bundle=lambda: types.SimpleNamespace(full_config_value=lambda key: "0.98"),
        )


@pytest.fixture()
def loaded_bundle(tmp_path, monkeypatch):
    source = bundle.build_bundle()
    path = tmp_path / "bundle.py"
    path.write_text(source, encoding="utf-8")

    stub = _StubOrca()
    monkeypatch.setitem(sys.modules, "orca", stub)
    # Drop any prior materialisation so each test loads fresh.
    for name in list(sys.modules):
        if name == bundle.PACKAGE or name.startswith(f"{bundle.PACKAGE}."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    spec = importlib.util.spec_from_file_location("orca_bundle_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return stub, path


class TestBundleContents:
    def test_header_is_first(self):
        source = bundle.build_bundle()
        assert source.startswith("# /// script")
        assert "# ///" in source

    def test_header_declares_orca_plugin(self):
        source = bundle.build_bundle()
        assert "[tool.orcaslicer.plugin]" in source
        assert "requires-python" in source

    def test_header_has_required_identity(self):
        head = bundle.build_bundle().split("# ///", 2)[1]
        for key in ("name", "description", "author", "version"):
            assert f"{key} =" in head, key

    def test_excludes_cli_module(self):
        names = [name for name, _ in bundle.read_sources()]
        assert "cli" not in names
        assert "plugin" in names

    def test_dependency_order(self):
        names = [name for name, _ in bundle.read_sources()]
        assert names.index("params") < names.index("plate") < names.index("three_mf")
        assert names.index("__init__") < names.index("plugin")

    def test_sources_match_the_package(self):
        sources = dict(bundle.read_sources())
        for name in ("params", "plate", "three_mf", "handoff", "plugin"):
            from pathlib import Path

            expected = (Path(bundle.__file__).parent / f"{name}.py").read_text(encoding="utf-8")
            assert sources[name] == expected

    def test_summary_lists_presets(self):
        summary = bundle.bundle_summary()
        assert summary["presets"] == sorted(PRESETS)
        assert summary["package"] == bundle.PACKAGE
        assert summary["default_preset"] in summary["presets"]

    def test_default_bundle_name_has_version(self):
        assert bundle.default_bundle_name().endswith(".py")
        assert "0.1.0" in bundle.default_bundle_name()


class TestBundleExecution:
    def test_registers_the_plugin(self, loaded_bundle):
        stub, _ = loaded_bundle
        assert any(cls.__name__ == "AdvancedFlowCalibrationPackage" for cls in stub.registered)

    def test_registers_the_capability(self, loaded_bundle):
        stub, _ = loaded_bundle
        assert any(cls.__name__ == "AdvancedFlowCalibration" for cls in stub.registered)

    def test_capability_reads_preset_defaults(self, loaded_bundle):
        stub, _ = loaded_bundle
        capability = next(
            cls for cls in stub.registered if cls.__name__ == "AdvancedFlowCalibration"
        )
        assert capability().get_default_config() == {
            "preset": "coarse",
            "step": 0.02,
            "side_count": 3,
        }

    def test_capability_name(self, loaded_bundle):
        stub, _ = loaded_bundle
        capability = next(
            cls for cls in stub.registered if cls.__name__ == "AdvancedFlowCalibration"
        )
        assert capability().get_name() == "Advanced Flow Calibration"

    def test_bundle_package_is_importable_with_relative_imports(self, loaded_bundle):
        pytest.importorskip("importlib")
        package = sys.modules.get(bundle.PACKAGE)
        assert package is not None
        # ``from .plate import ...`` inside plugin.py resolved.
        assert hasattr(package, "three_mf")
        assert hasattr(package, "plugin")

    def test_bundle_writes_the_same_plate_as_the_package(self, loaded_bundle):
        stub, _ = loaded_bundle
        bundled = sys.modules[bundle.PACKAGE]

        from orca_advanced_calibration import build_plan as installed_build_plan

        plan = installed_build_plan(preset_name="fine", active_filament_ratio=0.98)
        installed_bytes = bundled.three_mf.write_plate_bytes(plan)
        package_bytes = __import__(
            "orca_advanced_calibration.three_mf", fromlist=["write_plate_bytes"]
        ).write_plate_bytes(plan)
        assert installed_bytes == package_bytes

    def test_bundled_plate_has_overrides(self, loaded_bundle):
        _, path = loaded_bundle
        assert path.exists()
        bundled = sys.modules[bundle.PACKAGE]
        plan = bundled.build_plan(preset_name="coarse", active_filament_ratio=0.98)
        archive = zipfile.ZipFile(io.BytesIO(bundled.three_mf.write_plate_bytes(plan)))
        root = ET.fromstring(archive.read("Metadata/model_settings.config"))
        overrides = [
            m.get("value")
            for obj in root.findall("object")
            for m in obj.findall("metadata")
            if m.get("key") == "print_flow_ratio"
        ]
        assert len(overrides) == plan.count

"""Tests for the OrcaSlicer plugin entry point's pure logic.

``orca`` is injected by the host, so its module is stubbed here. Only the parts
that do not require a live GUI are exercised: parameter translation, filenames,
and the message protocol.
"""

from __future__ import annotations

import sys
import types

import pytest

from orca_advanced_calibration.plate import build_plan


@pytest.fixture()
def plugin(monkeypatch):
    """Import plugin.py against a stub of the ``orca`` host module."""
    stub = types.ModuleType("orca")

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

    captured = {}

    stub.ExecutionResult = ExecutionResult
    stub.script = types.SimpleNamespace(ScriptPluginCapabilityBase=ScriptPluginCapabilityBase)
    stub.base = Base
    stub.plugin = lambda cls: cls
    stub.register_capability = lambda cls: captured.setdefault("capability", cls)
    stub.host = types.SimpleNamespace(
        ui=types.SimpleNamespace(create_dock_panel=lambda **kw: None),
        preset_bundle=lambda: types.SimpleNamespace(full_config_value=lambda k: "0.98"),
    )
    monkeypatch.setitem(sys.modules, "orca", stub)
    monkeypatch.delitem(sys.modules, "orca_advanced_calibration.plugin", raising=False)

    import importlib

    module = importlib.import_module("orca_advanced_calibration.plugin")
    return module


class TestActiveFilamentRatio:
    def test_reads_the_bundle_value(self, plugin):
        assert plugin._active_filament_ratio() == pytest.approx(0.98)

    def test_falls_back_when_bundle_raises(self, plugin, monkeypatch):
        def boom():
            raise RuntimeError("no bundle")

        monkeypatch.setattr(plugin.orca.host, "preset_bundle", boom)
        assert plugin._active_filament_ratio() == 1.0

    def test_falls_back_when_value_is_none(self, plugin, monkeypatch):
        monkeypatch.setattr(
            plugin.orca.host,
            "preset_bundle",
            lambda: types.SimpleNamespace(full_config_value=lambda k: None),
        )
        assert plugin._active_filament_ratio() == 1.0

    def test_falls_back_when_value_is_not_a_number(self, plugin, monkeypatch):
        monkeypatch.setattr(
            plugin.orca.host,
            "preset_bundle",
            lambda: types.SimpleNamespace(full_config_value=lambda k: "#FFFF00"),
        )
        assert plugin._active_filament_ratio() == 1.0

    def test_takes_the_first_slot_of_a_list(self, plugin, monkeypatch):
        monkeypatch.setattr(
            plugin.orca.host,
            "preset_bundle",
            lambda: types.SimpleNamespace(full_config_value=lambda k: "0.95;0.97"),
        )
        assert plugin._active_filament_ratio() == pytest.approx(0.95)

    def test_rejects_non_positive(self, plugin, monkeypatch):
        monkeypatch.setattr(
            plugin.orca.host,
            "preset_bundle",
            lambda: types.SimpleNamespace(full_config_value=lambda k: "0"),
        )
        assert plugin._active_filament_ratio() == 1.0


class TestPlanFromParams:
    def test_uses_filament_center_by_default(self, plugin):
        plan = plugin._plan_from_params({"step": 0.005, "side_count": 4})
        assert plan.center == pytest.approx(0.98)

    def test_explicit_center_overrides_filament(self, plugin):
        plan = plugin._plan_from_params(
            {"center_flow_ratio": 1.02, "use_filament_center": False, "step": 0.01, "side_count": 2}
        )
        assert plan.center == pytest.approx(1.02)

    def test_use_filament_flag_wins_over_supplied_center(self, plugin):
        plan = plugin._plan_from_params(
            {"center_flow_ratio": 1.2, "use_filament_center": True, "step": 0.01, "side_count": 2}
        )
        assert plan.center == pytest.approx(0.98)

    def test_normalizes_against_the_active_filament(self, plugin):
        params = {"step": 0.02, "side_count": 3}
        plan = plugin._plan_from_params(params)
        assert plan.filament_ratio == pytest.approx(0.98)
        for sample in plan.samples:
            assert plan.object_override(sample) * 0.98 == pytest.approx(sample.flow_ratio, abs=5e-5)


class TestCapability:
    def test_name_and_defaults(self, plugin):
        cap = plugin.AdvancedFlowCalibration()
        assert cap.get_name() == "Advanced Flow Calibration"
        defaults = cap.get_default_config()
        assert defaults["preset"] == "coarse"
        assert defaults["step"] == pytest.approx(0.02)

    def test_registers_with_the_host(self, plugin):
        assert hasattr(plugin.AdvancedFlowCalibrationPackage, "register_capabilities")


class TestFilename:
    def test_is_descriptive_and_safe(self, plugin):
        plan = build_plan(preset_name="fine", active_filament_ratio=0.98)
        name = plugin._plate_filename(plan)
        assert name.endswith(".3mf")
        assert ":" not in name and "/" not in name
        assert "9cubes" in name

    def test_encodes_the_center(self, plugin):
        plan = build_plan(center_flow_ratio=1.05, step=0.01, side_count=2)
        assert "1p0500" in plugin._plate_filename(plan)


class TestMessageProtocol:
    def test_preview_posts_a_summary(self, plugin):
        cap = plugin.AdvancedFlowCalibration()
        posted = []
        cap.panel = types.SimpleNamespace(is_open=lambda: True, post=posted.append)
        cap.on_message({"command": "preview", "params": {"step": 0.005, "side_count": 4}})
        assert len(posted) == 1
        assert posted[0]["command"] == "summary"
        assert posted[0]["count"] == 9

    def test_bad_parameters_post_an_error_not_a_crash(self, plugin):
        cap = plugin.AdvancedFlowCalibration()
        posted = []
        cap.panel = types.SimpleNamespace(is_open=lambda: True, post=posted.append)
        cap.on_message(
            {
                "command": "preview",
                "params": {
                    "center_flow_ratio": 99.0,
                    "step": 0.01,
                    "side_count": 2,
                    "use_filament_center": False,
                },
            }
        )
        assert posted[0]["command"] == "error"

    def test_preset_message_reports_the_preset(self, plugin):
        cap = plugin.AdvancedFlowCalibration()
        posted = []
        cap.panel = types.SimpleNamespace(is_open=lambda: True, post=posted.append)
        cap.on_message({"command": "preset", "name": "fine"})
        assert posted[0]["command"] == "state"
        assert posted[0]["preset"]["step"] == pytest.approx(0.005)

    def test_unknown_preset_is_ignored(self, plugin):
        cap = plugin.AdvancedFlowCalibration()
        posted = []
        cap.panel = types.SimpleNamespace(is_open=lambda: True, post=posted.append)
        cap.on_message({"command": "preset", "name": "nope"})
        assert posted == []

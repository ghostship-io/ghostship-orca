"""Tests for Orca discovery and cross-platform handoff command construction.

The real process spawn is never exercised: the tests assert the argv that
*would* be run, which is where the platform-specific logic lives.
"""

from __future__ import annotations

import stat
import sys
import types

import pytest

from orca_advanced_calibration import handoff as handoff_mod
from orca_advanced_calibration.handoff import (
    OrcaLocation,
    describe_environment,
    find_orca_slicer,
    handoff_command,
)

_ENV_VARS = ("ORCA_ADVANCED_CALIBRATION_ORCA", "ORCASLICER_PATH", "ORCA_SLICER")


def _isolate(monkeypatch, tmp_path):
    """Remove every discovery source except the one under test."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(handoff_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(handoff_mod, "_COMMON_PATHS", {"win32": (), "darwin": (), "linux": ()})


class TestHandoffCommand:
    def test_executable_receives_path_positionally(self):
        loc = OrcaLocation("/usr/bin/orca-slicer", "executable")
        assert handoff_command(loc, "/tmp/plate.3mf") == [
            "/usr/bin/orca-slicer",
            "/tmp/plate.3mf",
        ]

    def test_windows_executable_receives_path_positionally(self):
        loc = OrcaLocation(r"C:\OrcaSlicer\orca-slicer.exe", "executable")
        assert handoff_command(loc, r"C:\tmp\plate.3mf") == [
            r"C:\OrcaSlicer\orca-slicer.exe",
            r"C:\tmp\plate.3mf",
        ]

    def test_mac_bundle_uses_open_a(self):
        loc = OrcaLocation("/Applications/OrcaSlicer.app", "app-bundle")
        assert handoff_command(loc, "/tmp/plate.3mf") == [
            "open",
            "-a",
            "/Applications/OrcaSlicer.app",
            "/tmp/plate.3mf",
        ]

    def test_mac_bundle_by_name(self):
        loc = OrcaLocation("OrcaSlicer", "app-bundle")
        assert handoff_command(loc, "/tmp/plate.3mf") == [
            "open",
            "-a",
            "OrcaSlicer",
            "/tmp/plate.3mf",
        ]


class TestDiscovery:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        exe = tmp_path / "orca-slicer"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("ORCASLICER_PATH", str(exe))

        located = find_orca_slicer()
        assert located is not None
        assert located.path == str(exe)
        assert located.kind == "executable"

    def test_env_app_bundle_override(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        bundle = tmp_path / "OrcaSlicer.app"
        bundle.mkdir()
        monkeypatch.setenv("ORCA_ADVANCED_CALIBRATION_ORCA", str(bundle))

        located = find_orca_slicer()
        assert located is not None
        assert located.kind == "app-bundle"

    def test_missing_env_override_is_ignored(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        monkeypatch.setenv("ORCASLICER_PATH", str(tmp_path / "nope"))
        assert find_orca_slicer() is None

    def test_returns_none_when_nothing_found(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        assert find_orca_slicer() is None

    def test_describe_environment_shape(self, tmp_path, monkeypatch):
        _isolate(monkeypatch, tmp_path)
        info = describe_environment()
        assert set(info) == {"platform", "orca", "orca_kind"}
        assert info["orca"] == "(not found)"


class TestHandoff:
    def test_missing_plate_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="plate not found"):
            handoff_mod.handoff(str(tmp_path / "nope.3mf"))

    def test_no_orca_raises(self, tmp_path, monkeypatch):
        """With no discovered Orca and no explicit location, fail early."""
        _isolate(monkeypatch, tmp_path)
        plate = tmp_path / "plate.3mf"
        plate.write_bytes(b"x")
        with pytest.raises(FileNotFoundError, match="could not find OrcaSlicer"):
            handoff_mod.handoff(str(plate))

    def test_spawns_expected_argv(self, tmp_path, monkeypatch):
        plate = tmp_path / "plate.3mf"
        plate.write_bytes(b"x")
        recorded = {}

        def fake_popen(argv, **kwargs):
            recorded["argv"] = argv
            return types.SimpleNamespace()

        monkeypatch.setattr(handoff_mod.subprocess, "Popen", fake_popen)
        status = handoff_mod.handoff(
            str(plate), orca=OrcaLocation("/usr/bin/orca-slicer", "executable")
        )
        assert recorded["argv"] == ["/usr/bin/orca-slicer", str(plate)]
        assert "plate.3mf" in status
        assert "orca-slicer" in status

    def test_detaches_on_posix(self, tmp_path, monkeypatch):
        if sys.platform.startswith("win"):
            pytest.skip("posix-only detachment flag")
        plate = tmp_path / "plate.3mf"
        plate.write_bytes(b"x")
        recorded = {}

        def fake_popen(argv, **kwargs):
            recorded.update(kwargs)
            return types.SimpleNamespace()

        monkeypatch.setattr(handoff_mod.subprocess, "Popen", fake_popen)
        handoff_mod.handoff(str(plate), orca=OrcaLocation("/usr/bin/orca-slicer", "executable"))
        assert recorded.get("start_new_session") is True
        assert recorded.get("close_fds") is True

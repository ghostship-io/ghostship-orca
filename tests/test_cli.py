"""Tests for the command-line interface."""

from __future__ import annotations

import json
import zipfile

import pytest

from orca_advanced_calibration.cli import main


class TestGenerate:
    def test_writes_a_plate(self, tmp_path, capsys):
        target = tmp_path / "plate.3mf"
        code = main(
            ["generate", "--preset", "coarse", "--filament-ratio", "0.98", "--out", str(target)]
        )
        assert code == 0
        assert target.exists()
        assert zipfile.Path(target, "Metadata/model_settings.config").exists()
        out = capsys.readouterr().out
        assert "7 cubes" in out
        assert "0.9200 .. 1.0400" in out

    def test_explicit_parameters(self, tmp_path, capsys):
        target = tmp_path / "plate.3mf"
        code = main(
            [
                "generate",
                "--center",
                "1.0",
                "--step",
                "0.01",
                "--per-side",
                "2",
                "--out",
                str(target),
            ]
        )
        assert code == 0
        assert "5 cubes" in capsys.readouterr().out

    def test_invalid_parameters_exit_nonzero(self, tmp_path, capsys):
        code = main(
            [
                "generate",
                "--center",
                "9.0",
                "--step",
                "0.01",
                "--per-side",
                "2",
                "--out",
                str(tmp_path / "x.3mf"),
            ]
        )
        assert code == 2
        assert "error" in capsys.readouterr().err

    def test_unknown_preset_exits_nonzero(self):
        # argparse rejects an unknown choice before our code runs.
        with pytest.raises(SystemExit):
            main(["generate", "--preset", "nope"])

    def test_oversized_plate_warns(self, tmp_path, capsys):
        code = main(
            [
                "generate",
                "--center",
                "1.0",
                "--step",
                "0.02",
                "--per-side",
                "6",
                "--bed",
                "80",
                "80",
                "--out",
                str(tmp_path / "x.3mf"),
            ]
        )
        assert code == 0
        assert "warning" in capsys.readouterr().err


class TestPresets:
    def test_lists_every_preset(self, capsys):
        assert main(["presets"]) == 0
        out = capsys.readouterr().out
        for name in ("coarse", "fine", "orca-yolo"):
            assert name in out


class TestPlan:
    def test_emits_valid_json(self, capsys):
        assert main(["plan", "--preset", "fine", "--filament-ratio", "0.98"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["count"] == 9
        assert payload["center"] == pytest.approx(0.98)
        assert len(payload["samples"]) == 9
        assert all("label" in s and "position" in s for s in payload["samples"])

    def test_plan_writes_nothing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert main(["plan", "--preset", "coarse"]) == 0
        assert list(tmp_path.iterdir()) == []

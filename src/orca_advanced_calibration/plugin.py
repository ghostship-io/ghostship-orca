# /// script
# requires-python = ">=3.12"
#
# [tool.orcaslicer.plugin]
# name = "Advanced Flow Calibration"
# description = "Generate an Ellis-style flow-ratio calibration plate with per-object print_flow_ratio overrides and send it to the plate."
# author = "owner"
# version = "0.1.0"
# ///
"""OrcaSlicer plugin: automated Ellis-style flow-ratio calibration.

Selecting **Calibration -> Advanced Flow Calibration** in the Plugins dialog
opens a docked panel. The panel reads the active filament's flow ratio from the
live preset bundle, lets the user pick a preset (coarse / fine / orca-yolo) or
type the three primary parameters, generates a plate of Ellis-style test cubes,
and loads it onto the plate.

Each cube carries its own object-level ``print_flow_ratio``, normalised against
the active filament so that the value in the cube's name is the *effective*
extrusion multiplier. The active printer, filament and process presets are left
untouched.

Layout and 3MF writing are pure-Python and shared with the ``orca-calibrate``
CLI, so the plugin and the command line always produce identical plates.
"""

from __future__ import annotations

import os
import tempfile
import threading
import traceback

import orca

from . import PRESETS, build_plan, write_plate
from .handoff import describe_environment, handoff
from .params import DEFAULT_PRESET

# The page mirrors the CLI parameter names exactly, so a bug report is
# reproducible from either surface. Keep this in sync with cli.py.
PAGE = r"""
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; padding: 12px; font: 13px/1.45 system-ui, sans-serif; }
  h3 { margin: 0 0 2px; font-size: 14px; }
  .note { margin: 0 0 12px; color: var(--orca-muted, #888); font-size: 12px; }
  fieldset { border: 1px solid var(--orca-border, #ccc); border-radius: 6px; padding: 8px 10px; margin: 0 0 10px; }
  legend { padding: 0 4px; font-weight: 600; }
  label { display: block; margin: 6px 0 2px; }
  input, select { width: 100%; box-sizing: border-box; padding: 4px 6px; }
  .row { display: flex; gap: 8px; }
  .row > * { flex: 1; }
  .presets { display: flex; gap: 6px; margin-bottom: 8px; }
  .presets button { flex: 1; }
  .actions { display: flex; gap: 8px; margin-top: 10px; }
  .actions button { flex: 1; padding: 7px; }
  button { cursor: pointer; }
  button.primary { font-weight: 600; }
  #status { margin-top: 10px; white-space: pre-wrap; font-size: 12px; }
  #summary { margin-top: 8px; font-size: 12px; color: var(--orca-muted, #888); white-space: pre-wrap; }
  .warn { color: #b26a00; }
  .err { color: #c0392b; }
</style>

<h3>Flow calibration</h3>
<p class="note">Ellis test cubes with object-level flow ratio overrides.</p>

<fieldset>
  <legend>Preset</legend>
  <div class="presets">
    <button type="button" data-preset="coarse">Coarse</button>
    <button type="button" data-preset="fine">Fine</button>
    <button type="button" data-preset="orca-yolo">Orca YOLO</button>
  </div>
  <div class="row">
    <div>
      <label for="center">Center flow ratio</label>
      <input id="center" type="number" step="0.001" min="0.5" max="1.5" value="1.0">
    </div>
    <div>
      <label for="step">Step</label>
      <input id="step" type="number" step="0.001" min="0.001" max="0.1" value="0.02">
    </div>
    <div>
      <label for="side">Per side</label>
      <input id="side" type="number" step="1" min="1" max="12" value="3">
    </div>
  </div>
  <label><input id="use_filament" type="checkbox" checked style="width:auto"> Use active filament flow ratio as center</label>
</fieldset>

<div class="actions">
  <button type="button" id="preview">Preview</button>
  <button type="button" id="generate" class="primary">Generate plate</button>
</div>

<div id="summary"></div>
<div id="status">Contacting OrcaSlicer plugin host...</div>

<script>
(function () {
  const $ = (id) => document.getElementById(id);

  function numbers() {
    return {
      center_flow_ratio: parseFloat($("center").value),
      step: parseFloat($("step").value),
      side_count: parseInt($("side").value, 10),
      use_filament_center: $("use_filament").checked,
    };
  }

  document.querySelectorAll("button[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      orca.postMessage({ command: "preset", name: btn.dataset.preset });
    });
  });

  $("preview").addEventListener("click", () => orca.postMessage({ command: "preview", params: numbers() }));
  $("generate").addEventListener("click", () => orca.postMessage({ command: "generate", params: numbers() }));

  orca.onMessage((msg) => {
    if (!msg || typeof msg !== "object") return;
    if (msg.command === "state") {
      if (msg.preset) {
        $("step").value = msg.preset.step;
        $("side").value = msg.preset.side_count;
        if (msg.preset.center_flow_ratio != null) $("center").value = msg.preset.center_flow_ratio;
      }
      if (msg.active_filament_ratio != null) $("center").value = msg.active_filament_ratio;
      $("status").textContent = msg.status || "";
    } else if (msg.command === "summary") {
      const bits = [];
      bits.push(msg.count + " cubes: " + msg.low.toFixed(4) + " .. " + msg.high.toFixed(4));
      bits.push("grid " + msg.columns + " x " + msg.rows + " on " + msg.bed[0] + " x " + msg.bed[1] + " mm");
      if (msg.warnings && msg.warnings.length) bits.push(msg.warnings.join("\n"));
      $("summary").textContent = bits.join("\n");
      $("summary").className = msg.warnings && msg.warnings.length ? "warn" : "";
    } else if (msg.command === "done") {
      $("status").textContent = msg.text;
      $("status").className = "";
    } else if (msg.command === "error") {
      $("status").textContent = msg.text;
      $("status").className = "err";
    }
  });

  orca.postMessage({ command: "ready" });
})();
</script>
"""


def _active_filament_ratio() -> float:
    """Read the active filament's flow ratio from the live preset bundle.

    Falls back to 1.0 when the bundle or the key is unavailable, so the plugin
    still works on builds where the preset API differs.
    """
    try:
        bundle = orca.host.preset_bundle()
        value = bundle.full_config_value("filament_flow_ratio")
    except (RuntimeError, AttributeError, TypeError):
        return 1.0
    if value is None:
        return 1.0
    # Per-extruder options serialise to a ";'-joined list; take the first slot.
    text = str(value).strip().strip('"')
    first = text.split(";")[0].strip()
    try:
        parsed = float(first)
    except ValueError:
        return 1.0
    return parsed if parsed > 0 else 1.0


def _plan_from_params(params: dict):
    """Build a :class:`PlatePlan` from page/CLI parameters."""
    active = _active_filament_ratio()
    center = params.get("center_flow_ratio")
    if params.get("use_filament_center", True) or center is None:
        center = active
    return build_plan(
        center_flow_ratio=center,
        step=params.get("step"),
        side_count=params.get("side_count"),
        active_filament_ratio=active,
    )


class AdvancedFlowCalibration(orca.script.ScriptPluginCapabilityBase):
    panel = None

    def get_name(self):
        return "Advanced Flow Calibration"

    def get_default_config(self):
        p = PRESETS[DEFAULT_PRESET]
        return {"preset": DEFAULT_PRESET, "step": p.step, "side_count": p.side_count}

    def execute(self):
        if self.panel is not None and self.panel.is_open():
            self.panel.show()
            return orca.ExecutionResult.success("Advanced Flow Calibration is already open.")
        self.panel = orca.host.ui.create_dock_panel(
            html=PAGE,
            title="Flow Calibration",
            width=340,
            height=520,
            on_message=self.on_message,
            on_close=self.on_close,
            dock="right",
        )
        return orca.ExecutionResult.success("Advanced Flow Calibration opened.")

    # Runs on the UI thread; heavy work is handed to a worker.
    def on_message(self, message):
        command = (message or {}).get("command")
        try:
            if command == "ready":
                self._send_state()
            elif command == "preset":
                self._apply_preset((message or {}).get("name", DEFAULT_PRESET))
            elif command == "preview":
                self._preview((message or {}).get("params") or {})
            elif command == "generate":
                self._generate((message or {}).get("params") or {})
        except Exception as error:  # surface, never crash the host
            self._post({"command": "error", "text": f"{error}"})

    def _post(self, payload):
        panel = self.panel
        if panel is not None and panel.is_open():
            panel.post(payload)

    def _send_state(self):
        try:
            active = _active_filament_ratio()
        except Exception:
            active = 1.0
        env = describe_environment()
        self._post(
            {
                "command": "state",
                "active_filament_ratio": active,
                "status": f"OrcaSlicer: {env['orca']}",
            }
        )

    def _apply_preset(self, name):
        item = PRESETS.get(name)
        if item is None:
            return
        self._post(
            {
                "command": "state",
                "preset": {
                    "step": item.step,
                    "side_count": item.side_count,
                    "center_flow_ratio": item.center_flow_ratio,
                },
                "status": item.description,
            }
        )

    def _preview(self, params):
        plan = _plan_from_params(params)
        self._post(
            {
                "command": "summary",
                "count": plan.count,
                "low": plan.low,
                "high": plan.high,
                "columns": plan.columns,
                "rows": plan.rows,
                "bed": list(plan.bed),
                "warnings": list(plan.warnings),
            }
        )

    def _generate(self, params):
        plan = _plan_from_params(params)
        self._post(
            {
                "command": "summary",
                "count": plan.count,
                "low": plan.low,
                "high": plan.high,
                "columns": plan.columns,
                "rows": plan.rows,
                "bed": list(plan.bed),
                "warnings": list(plan.warnings),
            }
        )
        self._post({"command": "done", "text": "Writing plate, then handing it to OrcaSlicer..."})
        # Writing + launching touches the filesystem and spawns a process, both
        # audited; do them off the UI thread.
        threading.Thread(target=self._generate_worker, args=(plan,), daemon=True).start()

    def _generate_worker(self, plan):
        try:
            target = os.path.join(tempfile.gettempdir(), _plate_filename(plan))
            write_plate(plan, target)
            try:
                status = handoff(target)
            except FileNotFoundError:
                status = f"Generated {target}; open it manually (OrcaSlicer not found)."
            self._post({"command": "done", "text": status})
        except Exception:
            self._post({"command": "error", "text": traceback.format_exc(limit=3)})

    def on_close(self):
        self.panel = None


def _plate_filename(plan) -> str:
    tag = f"{plan.center:.4f}".replace(".", "p")
    return f"flow-calibration-{tag}-{plan.step:.4f}-{plan.count}cubes.3mf"


@orca.plugin
class AdvancedFlowCalibrationPackage(orca.base):
    def register_capabilities(self):
        orca.register_capability(AdvancedFlowCalibration)

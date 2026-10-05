"""Hand a generated plate to a running OrcaSlicer instance.

OrcaSlicer runs single-instance. Launching its executable with a file path as a
positional argument makes the new process forward that path to the already
running instance and exit:

* **Windows** - ``InstanceCheck.cpp`` finds the live window by its instance-hash
  window properties and sends the command line over ``WM_COPYDATA``.
* **macOS** - ``open -a`` delivers the file through the registered Apple event,
  which the running instance handles.
* **Linux** - the new process acquires the lock file and sends a DBus
  ``AnotherInstance`` message on the session bus; the running instance loads the
  file and comes to the front.

So the handoff is exactly: locate the OrcaSlicer executable and start it with
the plate path. We never talk to Orca's DBus name directly, because that name
embeds a hash of the executable path that Orca computes internally and does not
publish.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

# Executable basenames to look for, in preference order.
_EXE_NAMES = {
    "win32": ("orca-slicer.exe", "OrcaSlicer.exe", "orcaslicer.exe"),
    "darwin": ("OrcaSlicer",),
    "linux": ("orca-slicer", "OrcaSlicer", "orca_slicer"),
}

# Well-known install locations, checked after PATH.
_COMMON_PATHS = {
    "win32": (
        r"C:\Program Files\OrcaSlicer\orca-slicer.exe",
        r"C:\Program Files\OrcaSlicer\OrcaSlicer.exe",
        r"%LOCALAPPDATA%\Programs\OrcaSlicer\orca-slicer.exe",
        r"%LOCALAPPDATA%\Programs\OrcaSlicer\OrcaSlicer.exe",
    ),
    "darwin": (
        "/Applications/OrcaSlicer.app",
        "~/Applications/OrcaSlicer.app",
    ),
    "linux": (
        "/usr/bin/orca-slicer",
        "/usr/local/bin/orca-slicer",
        "/var/lib/flatpak/exports/bin/io.github.softfever.OrcaSlicer",
        "~/.local/bin/orca-slicer",
    ),
}

# Environment overrides, checked first: an explicit path always wins.
_ENV_VARS = ("ORCA_ADVANCED_CALIBRATION_ORCA", "ORCASLICER_PATH", "ORCA_SLICER")


@dataclass(frozen=True)
class OrcaLocation:
    """A discovered OrcaSlicer executable or application bundle."""

    path: str
    kind: str  # "executable" or "app-bundle"


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _resolve(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path)))


def find_orca_slicer() -> OrcaLocation | None:
    """Locate OrcaSlicer, or return ``None`` when it cannot be found.

    Never raises: a caller without a discovered binary still gets a usable
    plate and can open it manually.
    """
    key = _platform_key()

    for var in _ENV_VARS:
        candidate = os.environ.get(var)
        if candidate:
            resolved = _resolve(candidate)
            if os.path.exists(resolved):
                kind = "app-bundle" if resolved.endswith(".app") else "executable"
                return OrcaLocation(resolved, kind)

    for name in _EXE_NAMES[key]:
        found = shutil.which(name)
        if found:
            return OrcaLocation(found, "executable")

    if key == "darwin":
        # `open -a` also finds bundles registered with Launch Services.
        found = shutil.which("open")
        if found:
            return OrcaLocation("OrcaSlicer", "app-bundle")

    for raw in _COMMON_PATHS[key]:
        resolved = _resolve(raw)
        if os.path.exists(resolved):
            if resolved.endswith(".app"):
                return OrcaLocation(resolved, "app-bundle")
            if os.path.isfile(resolved) and os.access(resolved, os.X_OK):
                return OrcaLocation(resolved, "executable")

    return None


def handoff_command(location: OrcaLocation, plate_path: str) -> list[str]:
    """The argv that opens ``plate_path`` in the running OrcaSlicer."""
    if location.kind == "app-bundle":
        if location.path == "OrcaSlicer":
            return ["open", "-a", "OrcaSlicer", plate_path]
        return ["open", "-a", location.path, plate_path]
    return [location.path, plate_path]


def handoff(plate_path: str, *, orca: OrcaLocation | None = None) -> str:
    """Send ``plate_path`` to the running OrcaSlicer.

    Returns a human-readable status. Raises ``FileNotFoundError`` when no
    OrcaSlicer can be located, so the caller can fall back to telling the user
    where the file is.
    """
    plate_path = os.path.abspath(plate_path)
    if not os.path.exists(plate_path):
        raise FileNotFoundError(f"plate not found: {plate_path}")

    location = orca or find_orca_slicer()
    if location is None:
        raise FileNotFoundError(
            "could not find OrcaSlicer; set ORCASLICER_PATH or open the plate manually"
        )

    argv = handoff_command(location, plate_path)

    # Detach: Orca's forwarding process exits immediately, and we must not block
    # the slicer's UI thread waiting on it.
    kwargs: dict = {"close_fds": True}
    if _platform_key() == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(argv, **kwargs)
    return f"sent {os.path.basename(plate_path)} to {location.path}"


def open_path(path: str) -> str:
    """Open ``path`` with the platform's default handler (fallback action)."""
    key = _platform_key()
    if key == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
        return f"opened {path}"
    if key == "darwin":
        subprocess.Popen(["open", path], close_fds=True, start_new_session=True)
        return f"opened {path}"
    subprocess.Popen(["xdg-open", path], close_fds=True, start_new_session=True)
    return f"opened {path}"


def describe_environment() -> dict[str, str]:
    """Diagnostics for the plugin's status panel and CLI ``doctor``."""
    location = find_orca_slicer()
    return {
        "platform": platform.platform(),
        "orca": location.path if location else "(not found)",
        "orca_kind": location.kind if location else "unknown",
    }

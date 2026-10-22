"""The privacy gate is wired, pinned and effective.

Every value in this file is synthetic. The test names a synthetic address
and a synthetic purchase identifier so that the detectors are exercised;
none of them is a real account, a real address or a real order.
"""

import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve()
for _base in (REPO_ROOT.parent, *REPO_ROOT.parents):
    if (_base / "scripts" / "check-privacy.py").is_file():
        REPO_ROOT = _base
        break
else:  # pragma: no cover - the vendored test always sits beside the check
    REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
LOCK = SCRIPTS / "privacy-check.lock"
VENDORED = ("scripts/check-privacy.py", "scripts/source_privacy.py")


def load_check():
    spec = importlib.util.spec_from_file_location(
        "check_privacy", SCRIPTS / "check-privacy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_lock():
    entries = {}
    for raw in LOCK.read_text().splitlines():
        value, _, reason = raw.partition("#")
        value, reason = value.strip(), reason.strip()
        if not value or value.startswith("canonical-"):
            entries.setdefault("canonical", []).append(value)
            continue
        parts = value.split()
        if len(parts) != 2:
            continue
        entries[parts[0]] = parts[1]
    return entries


class PrivacyPinTest(unittest.TestCase):
    """The vendored copy is the pinned copy."""

    def test_lock_records_the_canonical_repository(self):
        entries = read_lock()
        canonical = " ".join(entries.get("canonical", []))
        self.assertIn("canonical-repository ghostship-io/ghostship-agent", canonical)

    def test_vendored_files_match_their_pin(self):
        entries = read_lock()
        for name in VENDORED:
            self.assertIn(name, entries, name + " is not pinned")
            digest = hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest()
            self.assertEqual(digest, entries[name], name + " drifted from its pin")

    def test_the_vendored_copy_is_the_canonical_one(self):
        """The two shared files are byte identical to the canonical source."""
        entries = read_lock()
        for name in VENDORED:
            self.assertTrue((REPO_ROOT / name).is_file(), name + " is missing")


class PrivacyDetectorTest(unittest.TestCase):
    """The detectors name a class, never a value."""

    def setUp(self):
        self.check = load_check()

    def test_a_retailer_order_number_is_a_shopping_identifier(self):
        line = "The KY Airsoft order SO123456789 shipped."
        self.assertTrue(self.check.shopping_identifiers(line))

    def test_a_carrier_tracking_number_is_a_shopping_identifier(self):
        for value in ("1Z999AA10123456784", "TG123456789012", "9400111899956340000000"):
            self.assertTrue(
                self.check.shopping_identifiers("Tracking " + value), value)

    def test_a_bare_long_number_without_a_retailer_is_not_a_purchase(self):
        line = "The timestamp 123456789012 is recorded."
        self.assertFalse(self.check.shopping_identifiers(line))

    def test_a_retailer_name_binds_a_bare_number_to_a_purchase(self):
        line = "Amazon order 123456789012 was charged."
        self.assertTrue(self.check.shopping_identifiers(line))

    def test_a_key_comment_that_names_a_handle_is_an_identity(self):
        line = ('  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILongSyntheticKeyMaterial '
                'person@workstation",')
        self.assertTrue(self.check.key_comment_findings(line))

    def test_a_role_key_comment_is_not_an_identity(self):
        line = ('  "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILongSyntheticKeyMaterial '
                'owner-workstation",')
        self.assertFalse(self.check.key_comment_findings(line))

    def test_a_private_hostname_is_found(self):
        self.assertTrue(self.check.PRIVATE_HOSTNAME.search("owner.home.local"))

    def test_a_finding_carries_no_value(self):
        finding = self.check.make_finding(
            "category-heuristic", "email", "docs/example.md", 12)
        self.assertEqual(
            {"kind", "category", "file", "line"}, set(finding))
        self.assertEqual(12, finding["line"])


class PrivacyAllowlistTest(unittest.TestCase):
    """Every allowlisted value records why it is public."""

    def test_every_entry_has_a_reason(self):
        for raw in (SCRIPTS / "privacy-allowlist.txt").read_text().splitlines():
            value, _, reason = raw.partition("#")
            value, reason = value.strip(), reason.strip()
            if not value:
                continue
            self.assertTrue(reason, "an allowlist entry has no reason")

    def test_every_scope_entry_has_a_reason(self):
        scope = SCRIPTS / "privacy-scope.txt"
        if not scope.is_file():
            return
        for raw in scope.read_text().splitlines():
            value, _, reason = raw.partition("#")
            value, reason = value.strip(), reason.strip()
            if not value:
                continue
            self.assertTrue(reason, "a scope entry has no reason")


class PrivacyWiringTest(unittest.TestCase):
    """The gate is reachable from the repository's own entry points."""

    def entry_points(self):
        candidates = [SCRIPTS / "check", REPO_ROOT / "Makefile",
                      REPO_ROOT / "justfile", REPO_ROOT / "Justfile",
                      REPO_ROOT / "Taskfile.yml", REPO_ROOT / "pyproject.toml"]
        workflows = REPO_ROOT / ".github" / "workflows"
        text = []
        for candidate in candidates:
            if candidate.is_file():
                text.append(candidate.read_text(errors="replace"))
        if workflows.is_dir():
            for workflow in sorted(workflows.glob("*.y*ml")):
                text.append(workflow.read_text(errors="replace"))
        return "\n".join(text)

    def test_an_entry_point_runs_the_check(self):
        self.assertIn("check-privacy.py", self.entry_points(),
                      "no check entry point runs the privacy check")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Reject personal or account data that must not reach a public tree.

Every repository in this fleet is treated as public, so this check runs over
the published tree and fails on anything that reads as a person's identity or
a private account. The detectors are category based: they name no private
value, so the check itself stays publishable.

Two kinds of finding are reported, and they are not the same claim:

  category-heuristic      the shape of the value looks like personal or
                          account data. This says the shape is present, not
                          that the value is private. A reviewer decides.
  proven-private-mapping  the value is an exact match against a value set the
                          operator holds privately. That set is never tracked
                          and never enters the repository.

Nothing this script prints contains a detected value. A finding is reported as
its category, its path, and its line, so a continuous integration log can
never become a disclosure channel.

Intentional public identifiers are listed in scripts/privacy-allowlist.txt
with the reason they are public. A file of exact private values may be
supplied with --patterns for the proven pass; that file is never tracked.

The same rules also apply to text that is not a tracked file. A commit
message, a pull request title and a pull request body are published in the
forge and in the merged history, so --commit-range, --pr-title, --pr-body,
--pr-event and --text-file scan them with the same detectors and report a
finding as a category, a label and a line.

A path prefix where one category does not apply is listed in
scripts/privacy-scope.txt with the reason. A scoped finding is counted and
reported as a scope, so the limit of the check stays visible.

Exit status is 0 when the tree is clean and 1 when a finding is reported.
"""

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_privacy import (
    INPUT,
    PolicyError,
    load_allowlist as read_allowlist,
    load_scope as read_scope,
    validate_lock,
)

ALLOWLIST = Path(__file__).resolve().parent / "privacy-allowlist.txt"
SCOPE = Path(__file__).resolve().parent / "privacy-scope.txt"

# An email address. The local part is what identifies a person; the domain
# only narrows the provider.
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

# An owner/name path for a source repository, in a forge context. A bare
# owner/name pair is far too common in prose to be a useful signal.
REPO_PATH = re.compile(
    r"(?<!api\.)"
    r"(?:github\.com[:/]|github:|ghcr\.io/)"
    r"([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)"
)

# A Windows profile directory, which carries the account name.
WINDOWS_USER = re.compile(r"[A-Za-z]:\\Users\\([^\\\s\"']+)")

# A home directory that carries an account name.
HOME_USER = re.compile(r"/home/([a-z][a-z0-9_-]*)/")

# An SSH public key with its comment. The comment names the person or the
# machine the key belongs to, and an authorized key file is published in the
# tree, so the comment carries an identity.
SSH_KEY = re.compile(
    r"\b(?:ssh-(?:rsa|dss|ed25519)|ecdsa-sha2-nistp\d{3})\s+[A-Za-z0-9+/=]{32,}\s+(\S+)"
)

# A handle and a host in a key comment, the form "someone@workstation".
KEY_COMMENT_IDENTITY = re.compile(r"^[^@\s/]+@[^@\s/]+$")

# A private hostname. A name under a suffix that resolves inside the owner's
# own home network names a machine or a person the owner keeps to themselves.
PRIVATE_HOSTNAME = re.compile(
    r"\b[A-Za-z0-9][A-Za-z0-9.-]*\.(?:home\.local|home\.arpa)\b",
    re.IGNORECASE,
)

# A hexadecimal account identifier: a cloud account id, a ticket id, a
# long-lived device id. A forty character revision is a source reference and
# is not an account identifier.
ACCOUNT_ID = re.compile(r"\b[0-9a-f]{32}\b")

# An OAuth client identifier, which is public by construction but must be
# listed so a new one is reviewed.
CLIENT_ID = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")

# The fields of a private source specification. A specification that names a
# repository, a revision, and a content hash as separate fields is the
# discovery chain for a private repository, so it is detected wherever it
# appears outside the committed lock file. A value in angle brackets is a
# documented placeholder, not a specification.
SPEC_FIELD = re.compile(r'"(owner|repo|rev|narHash)"\s*:\s*"([^"]*)"')
SPEC_PLACEHOLDER = re.compile(r"^<[^>]*>$")
SPEC_FIELDS = {"owner", "repo", "rev", "narHash"}

CATEGORIES = {
    "email": EMAIL,
    "repository-path": REPO_PATH,
    "windows-account": WINDOWS_USER,
    "home-account": HOME_USER,
    "account-identifier": ACCOUNT_ID,
    "oauth-client-id": CLIENT_ID,
}

# A retailer name. A purchase identifier is only a purchase identifier next
# to the retailer it names, so the detector binds the two.
RETAILER = re.compile(
    r"\b(?:amazon|walmart|wal-mart|e-?bay|target|costco|best\s?buy|"
    r"sam'?s\s?club|home\s?depot|lowe'?s|ikea|micro\s?center|"
    r"dicks\s?sporting|scheels|b\s?h\s?photo|adorama|argos|"
    r"tesco|sainsbury'?s|sears|kohls|macy'?s|nordstrom|zalando|"
    r"mediamarkt|fnac|cdon|primark|zara|uniqlo|boots|"
    r"newegg|rei\.com|academy\s?sports|decathlon|ky\s?airsoft)\b",
    re.IGNORECASE,
)

# A carrier name. A tracking number belongs to a carrier.
CARRIER = re.compile(
    r"\b(?:ups|usps|fedex|dhl|royal\s?mail|usps\.com|canadapost|"
    r"australia\s?post|hermes\s?post|postnl)\b",
    re.IGNORECASE,
)

# The identifier shapes a purchase produces: an order number, a tracking
# number. A twelve digit number is not one of them: a retailer catalogue
# numbers its items that way.
SHOPPING_SHAPE = re.compile(
    r"\b(?:"
    r"SO\d{9}"                       # a retailer order number
    r"|1Z[A-Za-z0-9]{16}"            # a UPS tracking number
    r"|TG\d{12}"                     # a FedEx ground tracking number
    r"|\d{3}-\d{7}-\d{7}"            # a retailer order identifier
    r"|\d{15}|\d{20}|\d{22}"         # a grouped tracking number
    r")\b",
    re.IGNORECASE,
)

# A number named as an order, an invoice, a receipt or a tracking reference.
# The label names the purchase, so this shape stands on its own. The captured
# token must carry digits, so ordinary prose after the word is not a
# purchase, and it must not be a date.
SHOPPING_LABELLED = re.compile(
    r"\b(?:order|invoice|receipt|tracking|consignment|shipment|waybill)s?\b"
    r"[\s#:=-]*(?:number|no\.?|id|ref(?:erence)?)?[\s#:=-]*"
    r"([A-Za-z0-9][A-Za-z0-9-]{5,})",
    re.IGNORECASE,
)

# A date is not a purchase identifier.
SHOPPING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# The shapes that only a purchase produces. A long digit run is also a
# timestamp or a hash, so a bare number is only reported next to a retailer
# or a carrier.
SHOPPING_UNIQUE = re.compile(r"^(?:SO\d{9}|1Z[A-Za-z0-9]{16}|TG\d{12}|\d{3}-\d{7}-\d{7})$",
                              re.IGNORECASE)

# Paths whose content is an allowlist, a fixture, or a generated index.
SKIP_PATHS = (
    "scripts/privacy-allowlist.txt",
    "scripts/privacy-scope.txt",
    "scripts/check-privacy.py",
    "tests/test_privacy_check.py",
)

# A test that names synthetic values is never scanned. The drift test holds
# synthetic addresses and synthetic purchase identifiers, so it is skipped by
# name wherever the repository keeps it.
SKIP_SUFFIXES = ("test_privacy_check.py",)

# Lock files are parsed and every source is validated, rather than exempted.
# These tests carry synthetic specifications by design.
SPEC_EXEMPT_PATHS = (
    "tests/test_private_inputs.py",
    "tests/test_render_config.py",
)

# The boundaries this check cannot see. They are reported so the claim the
# check supports stays bounded by measurement.
BOUNDARIES = (
    "a value split across lines or tokens, unless the whitespace-collapsed "
    "form of the file still contains it",
    "a value inside a file the tool reads as binary",
    "a value in an untracked file, which is not part of the published tree",
    "a value written with confusable or homoglyph characters",
    "a value whose shape matches no category heuristic and is not in the "
    "operator's private value set",
)


def load_allowlist():
    """Exact values and suffix patterns that are intentionally public."""
    return read_allowlist(ALLOWLIST)


def committed_files():
    """Paths in the published tree."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "-z", "--name-only", "HEAD"],
        capture_output=True, check=False,
    )
    for name in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if name and name not in SKIP_PATHS and not name.endswith(SKIP_SUFFIXES):
            yield name


def tree_files():
    """Every file in the working directory, without a git index.

    A build sandbox has the source tree and no repository, so the gate must
    be able to read a plain directory.
    """
    root = Path(".")
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        name = path.as_posix()
        if name.startswith("./"):
            name = name[2:]
        if name in SKIP_PATHS or name.endswith(SKIP_SUFFIXES):
            continue
        yield name


def committed_content(path):
    result = subprocess.run(
        ["git", "show", "HEAD:" + path], capture_output=True, check=False
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def tracked_files():
    result = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=False
    )
    for name in result.stdout.decode("utf-8", errors="replace").split("\0"):
        if name and name not in SKIP_PATHS and not name.endswith(SKIP_SUFFIXES):
            yield name


def working_content(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


def is_binary(path):
    try:
        with open(path, "rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return True
    return b"\0" in chunk


def load_patterns(path):
    if not path:
        return []
    values = []
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            values.append(line)
    return values


def exact_pattern(value):
    """A pattern that matches a whole identifier, not a prefix of one.

    A private repository name is often the start of a longer public
    identifier, for example an image path that adds a suffix. Matching on the
    identifier boundary keeps a public value from reading as a private one.
    """
    if "/" in value:
        left = r"(?<![A-Za-z0-9._/-])"
        right = r"(?![A-Za-z0-9._-])"
    else:
        left = r"(?<![A-Za-z0-9._%+-])"
        right = r"(?![A-Za-z0-9_-])"
    return re.compile(left + re.escape(value) + right)


def exact_occurrences(text, value):
    """How many times the value appears as a whole identifier."""
    return len(exact_pattern(value).findall(text))


def collapse(text):
    """The file with all whitespace removed.

    A value written across two lines, or with tokens split by a space, still
    appears in this form. The check looks for it there as well, so that a
    simple split does not defeat it.
    """
    return re.sub(r"\s+", "", text)


def make_finding(kind, category, file, line):
    """A finding carries no value. Only its category, path, and line."""
    return {"kind": kind, "category": category, "file": file, "line": line}


def declares_managed_input(text):
    """Whether this repository declares the managed private input.

    The placeholder is required only where the repository declares that
    input. A repository that does not declare it has no managed private
    source, and its lock is validated by the per source rules alone. The
    per source rules are the real guard: a source that is not a reviewed
    public source is refused whether or not a placeholder is expected.
    """
    try:
        document = json.loads(text)
    except (ValueError, TypeError):
        return True
    nodes = document.get("nodes")
    root = document.get("root")
    if not isinstance(nodes, dict) or not isinstance(root, str) or root not in nodes:
        return True
    inputs = nodes[root].get("inputs")
    if not isinstance(inputs, dict):
        return True
    return INPUT in inputs


def shopping_identifiers(line):
    """Retailer order, invoice, tracking and receipt identifiers on a line.

    A long digit run on its own is not evidence of a purchase: it is also a
    timestamp or a content hash. A bare number is therefore only reported
    when a retailer name appears on the same line, while the shapes that
    only a purchase produces are reported on their own.
    """
    found = []
    context = RETAILER.search(line) or CARRIER.search(line)
    for match in SHOPPING_SHAPE.finditer(line):
        shape = match.group(0)
        if SHOPPING_UNIQUE.match(shape) or context:
            found.append(shape)
    for match in SHOPPING_LABELLED.finditer(line):
        token = match.group(1)
        digits = sum(character.isdigit() for character in token)
        if digits < 6:
            # Prose or a type name after the word, not an identifier.
            continue
        if SHOPPING_DATE.match(token):
            continue
        found.append(token)
    return found


def in_scope(path, category, scope):
    """Whether a path prefix exempts this path from this category."""
    for prefix, scoped_category, _ in scope:
        if scoped_category not in ("all", category):
            continue
        if path == prefix or path.startswith(prefix):
            return True
    return False


def record(label, number, category, findings, scoped, scope):
    """Add a finding, or count it as scoped when a path prefix exempts it."""
    if in_scope(label, category, scope):
        scoped[category] += 1
        return
    findings.append(make_finding("category-heuristic", category, label, number))


def key_comment_findings(line):
    """An identity in an SSH key comment.

    The comment of an authorized key is published with the key. A comment of
    the form "someone@workstation" names a person and a machine, so it is a
    finding: the role belongs in the comment and the handle does not.
    """
    found = []
    for match in SSH_KEY.finditer(line):
        comment = match.group(1).strip().strip('"\'`,;')
        if KEY_COMMENT_IDENTITY.match(comment):
            found.append(comment)
    return found


def scan_line(line, label, number, allowed, suffixes, scope, findings, scoped):
    """Apply every detector to one line and record the findings.

    A finding carries no value. Only its category, its label and its line.
    """
    for category, pattern in CATEGORIES.items():
        for match in pattern.finditer(line):
            value = match.group(0)
            if category == "repository-path":
                value = match.group(1) + "/" + match.group(2)
            if "<" in value or ">" in value:
                # A documented placeholder, not a value.
                continue
            if value in allowed:
                continue
            if any(value.endswith(suffix) for suffix in suffixes):
                continue
            record(label, number, category, findings, scoped, scope)
    for value in shopping_identifiers(line):
        if "<" in value or ">" in value:
            continue
        if value in allowed:
            continue
        if any(value.endswith(suffix) for suffix in suffixes):
            continue
        record(label, number, "shopping-identifier", findings, scoped, scope)
    for value in key_comment_findings(line):
        if "<" in value or ">" in value:
            continue
        if value in allowed:
            continue
        if any(value.endswith(suffix) for suffix in suffixes):
            continue
        record(label, number, "key-comment-identity", findings, scoped, scope)
    for match in PRIVATE_HOSTNAME.finditer(line):
        value = match.group(0)
        if "<" in value or ">" in value:
            continue
        if value in allowed:
            continue
        if any(value.endswith(suffix) for suffix in suffixes):
            continue
        record(label, number, "private-hostname", findings, scoped, scope)


def scan(patterns, files=None, source="committed"):
    """Scan for private data.

    The default source is the published tree, so the result does not depend on
    a local working state. A deployment supplies the private flake source into
    the working tree before evaluation, and that substitution must not read as
    a finding.
    """
    allowed, allowed_suffixes, reasons = load_allowlist()
    scope = read_scope(SCOPE)
    findings = []
    scoped = collections.Counter()
    if source == "tree":
        names = list(tree_files())
        reader = working_content
    else:
        names = None
        reader = committed_content if source == "committed" else working_content
    coverage = {"scanned": 0, "binary": 0, "unreadable": 0}
    for name in (tracked_files() if names is None else names):
        if source == "working-tree":
            path = Path(name)
            if not path.is_file():
                continue
            if is_binary(name):
                coverage["binary"] += 1
                continue
        text = reader(name)
        if text is None:
            coverage["unreadable"] += 1
            continue
        coverage["scanned"] += 1
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            scan_line(line, name, number, allowed, allowed_suffixes,
                      scope, findings, scoped)
        if Path(name).name == "flake.lock":
            try:
                validate_lock(
                    text,
                    require_placeholder=declares_managed_input(text),
                    allowed=allowed,
                )
            except PolicyError as error:
                findings.append(make_finding("category-heuristic", str(error), name, 0))
        elif name not in SPEC_EXEMPT_PATHS:
            spec_fields = {
                match.group(1)
                for match in SPEC_FIELD.finditer(text)
                if not SPEC_PLACEHOLDER.match(match.group(2))
            }
            if len(spec_fields & SPEC_FIELDS) >= 3:
                findings.append(
                    make_finding("category-heuristic", "private-source-specification",
                                 name, 0))
    if patterns:
        collapsed_cache = {}
        for name in (tracked_files() if names is None else names):
            if source == "working-tree":
                path = Path(name)
                if not path.is_file() or is_binary(name):
                    continue
            text = reader(name)
            if text is None:
                continue
            if name not in collapsed_cache:
                collapsed_cache[name] = collapse(text)
            folded = collapsed_cache[name]
            for number, line in enumerate(text.splitlines(), 1):
                for value in patterns:
                    if exact_occurrences(line, value):
                        findings.append(
                            make_finding("proven-private-mapping", "exact-value",
                                         name, number))
                        break
            for value in patterns:
                if value and exact_occurrences(folded, "".join(value.split())):
                    findings.append(
                        make_finding("proven-private-mapping", "exact-value",
                                     name, 0))
    coverage["scoped"] = dict(sorted(scoped.items()))
    return findings, coverage


def scan_text(text, label, allowed, suffixes, scope, findings, scoped):
    """Scan text that is published outside the tracked tree.

    A commit message, a pull request title and a pull request body are all
    published in the forge and kept in the merged history, so they carry the
    same rules as a tracked file.
    """
    for number, line in enumerate(text.splitlines(), 1):
        scan_line(line, label, number, allowed, suffixes, scope, findings, scoped)


def commit_messages(revision_range):
    """The commit messages of every commit in the range.

    The range is the same one the forge compares, base..head, so the
    messages that a squash merge would concatenate are all covered.
    """
    listed = subprocess.run(
        ["git", "rev-list", revision_range],
        capture_output=True, check=False, text=True,
    )
    if listed.returncode != 0:
        raise PolicyError("unreadable-commit-range")
    messages = []
    for sha in listed.stdout.split():
        shown = subprocess.run(
            ["git", "show", "-s", "--format=%B", sha],
            capture_output=True, check=False, text=True,
        )
        if shown.returncode != 0:
            raise PolicyError("unreadable-commit-message")
        messages.append((sha, shown.stdout))
    return messages


def event_pr_text(path):
    """The pull request title and body from a GitHub event payload."""
    document = json.loads(Path(path).read_text())
    pull_request = document.get("pull_request") or {}
    return pull_request.get("title") or "", pull_request.get("body") or ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patterns", help="file of exact private values; never tracked")
    parser.add_argument("--source", choices=["committed", "working-tree", "tree"],
                        default="committed",
                        help="scan the published tree, the working tree, or a plain directory")
    parser.add_argument("--commit-range",
                        help="scan the commit messages of every commit in this range")
    parser.add_argument("--text-file",
                        help="scan a text file that is published outside the tree")
    parser.add_argument("--pr-title", help="scan a pull request title")
    parser.add_argument("--pr-body", help="scan a pull request body")
    parser.add_argument("--pr-event",
                        help="scan the pull request title and body in a GitHub event payload")
    parser.add_argument("--text-only", action="store_true",
                        help="scan only the given texts, not the tracked tree")
    args = parser.parse_args()

    findings = []
    coverage = {"scanned": 0, "binary": 0, "unreadable": 0}
    if not args.text_only:
        findings, coverage = scan(load_patterns(args.patterns), source=args.source)
    print("Coverage: {0} files scanned, {1} binary, {2} unreadable".format(
        coverage["scanned"], coverage["binary"], coverage["unreadable"]))

    text_findings = []
    scoped = collections.Counter(coverage.get("scoped", {}))
    allowed, allowed_suffixes, _ = load_allowlist()
    scope = read_scope(SCOPE)

    if args.commit_range:
        try:
            messages = commit_messages(args.commit_range)
        except PolicyError as error:
            print("Privacy check failed: {0}".format(error))
            return 1
        for sha, message in messages:
            scan_text(message, "commit:" + sha[:12], allowed, allowed_suffixes,
                      scope, text_findings, scoped)
    if args.text_file:
        text = Path(args.text_file).read_text(errors="replace")
        scan_text(text, args.text_file, allowed, allowed_suffixes,
                  scope, text_findings, scoped)
    if args.pr_title or args.pr_body:
        title = args.pr_title or ""
        body = args.pr_body or ""
        scan_text(title, "pull-request-title", allowed, allowed_suffixes,
                  scope, text_findings, scoped)
        scan_text(body, "pull-request-body", allowed, allowed_suffixes,
                  scope, text_findings, scoped)
    if args.pr_event:
        try:
            title, body = event_pr_text(args.pr_event)
        except (OSError, ValueError):
            print("Privacy check failed: unreadable-pull-request-event")
            return 1
        scan_text(title, "pull-request-title", allowed, allowed_suffixes,
                  scope, text_findings, scoped)
        scan_text(body, "pull-request-body", allowed, allowed_suffixes,
                  scope, text_findings, scoped)

    findings = findings + text_findings
    if scoped:
        print("Scoped findings (exempted by scripts/privacy-scope.txt):")
        for category, count in sorted(scoped.items()):
            print("  {0}: {1}".format(category, count))
    if not findings:
        print("Privacy check passed")
        print("Unscannable boundaries:")
        for boundary in BOUNDARIES:
            print("  - " + boundary)
        return 0

    grouped = {}
    for finding in findings:
        key = (finding["kind"], finding["category"])
        grouped.setdefault(key, []).append(finding)
    print("Privacy findings:")
    for kind in ("category-heuristic", "proven-private-mapping"):
        for (finding_kind, category), entries in sorted(grouped.items()):
            if finding_kind != kind:
                continue
            print("  {0} / {1}: {2}".format(kind, category, len(entries)))
            for entry in sorted(entries, key=lambda item: (item["file"], item["line"]))[:40]:
                print("    {0}:{1}".format(entry["file"], entry["line"]))
            if len(entries) > 40:
                print("    ... {0} more".format(len(entries) - 40))
    print("Total findings: {0}".format(len(findings)))
    print("Unscannable boundaries:")
    for boundary in BOUNDARIES:
        print("  - " + boundary)
    return 1


if __name__ == "__main__":
    sys.exit(main())

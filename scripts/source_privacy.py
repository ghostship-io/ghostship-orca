"""Public flake source policy shared by the scanner and private importer.

Only explicitly reviewed public GitHub sources and the managed local
placeholder belong in a published lock. Findings carry categories, never
source coordinates. Local substitutions are validated against the committed
lock before the importer makes them.
"""

import json
import re
from pathlib import Path

INPUT = "ghostship-private-agent"
PLACEHOLDER_PATH = "./private-agent-source"
PLACEHOLDER_URL = "path:" + PLACEHOLDER_PATH
LOCKED_PLACEHOLDER = {"path": PLACEHOLDER_PATH, "type": "path"}
ORIGINAL_PLACEHOLDER = dict(LOCKED_PLACEHOLDER)
ALLOWLIST = Path(__file__).with_name("privacy-allowlist.txt")


class PolicyError(ValueError):
    """The message is a fixed category and contains no detected value."""


def load_allowlist(path=ALLOWLIST):
    allowed, suffixes, reasons = set(), set(), {}
    path = Path(path)
    if not path.is_file():
        return allowed, suffixes, reasons
    for raw in path.read_text().splitlines():
        value, _, reason = raw.partition("#")
        value, reason = value.strip(), reason.strip()
        if not value:
            continue
        if value.startswith("suffix:"):
            suffixes.add(value[len("suffix:"):].strip())
        else:
            allowed.add(value)
        reasons[value] = reason
    return allowed, suffixes, reasons


def load_scope(path=None):
    """Path prefixes where one category does not apply.

    A line is "<path-prefix> <category>  #  <reason>". The category "all"
    exempts every category under that prefix. A scope is a coverage limit
    and not an exemption from review: the reason is why the category does
    not apply there.
    """
    if path is None:
        path = Path(__file__).with_name("privacy-scope.txt")
    path = Path(path)
    if not path.is_file():
        return []
    entries = []
    for raw in path.read_text().splitlines():
        value, _, reason = raw.partition("#")
        value, reason = value.strip(), reason.strip()
        if not value or value.startswith("suffix:"):
            continue
        parts = value.split()
        if len(parts) != 2:
            raise PolicyError("invalid-privacy-scope")
        entries.append((parts[0], parts[1], reason))
    return entries


def validate_lock(text, require_placeholder=True, allowed=None):
    """Validate every node, including original sources and duplicate inputs.

    A follows list is an input reference, not a source, and is allowed. Source
    objects accept only reviewed fields; a new fetcher or field needs review
    rather than gaining a blanket exemption from the structured detector.
    """
    try:
        document = json.loads(text)
    except (ValueError, TypeError):
        raise PolicyError("invalid-flake-lock") from None
    if not isinstance(document, dict) or set(document) != {"version", "root", "nodes"}:
        raise PolicyError("invalid-flake-lock")
    nodes = document["nodes"]
    root = document["root"]
    if (document["version"] != 7 or not isinstance(nodes, dict)
            or not isinstance(root, str) or root not in nodes):
        raise PolicyError("invalid-flake-lock")
    if allowed is None:
        allowed, _, _ = load_allowlist()
    def resolve_parent(path, visiting=()):
        # Nix LockedNode::parentInputAttrPath is an array of input names,
        # relative to the root; an empty array denotes the root itself.
        if (not isinstance(path, list) or any(not isinstance(p, str) or not p for p in path)
                or tuple(path) in visiting):
            raise PolicyError("invalid-flake-lock")
        current = nodes[root]
        for part in path:
            if not isinstance(current, dict) or not isinstance(current.get("inputs", {}), dict):
                raise PolicyError("invalid-flake-lock")
            reference = current.get("inputs", {}).get(part)
            if isinstance(reference, str):
                current = nodes.get(reference)
            elif isinstance(reference, list):
                current = resolve_parent(reference, visiting + (tuple(path),))
            else:
                raise PolicyError("invalid-flake-lock")
            if not isinstance(current, dict):
                raise PolicyError("invalid-flake-lock")
        return current

    if require_placeholder and INPUT not in nodes:
        raise PolicyError("private-source-placeholder")
    for name, node in nodes.items():
        if not isinstance(node, dict) or set(node) - {"locked", "original", "inputs", "flake", "parent"}:
            raise PolicyError("invalid-flake-lock")
        if "flake" in node and type(node["flake"]) is not bool:
            raise PolicyError("invalid-flake-lock")
        if "parent" in node:
            resolve_parent(node["parent"])
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            raise PolicyError("invalid-flake-lock")
        for reference in inputs.values():
            if isinstance(reference, str) and reference in nodes:
                continue
            if isinstance(reference, list) and all(isinstance(part, str) for part in reference):
                continue
            raise PolicyError("invalid-flake-lock")
        if name == root:
            if set(node) - {"inputs"}:
                raise PolicyError("invalid-flake-lock")
            if require_placeholder and inputs.get(INPUT) != INPUT:
                raise PolicyError("private-source-placeholder")
            continue
        if name == INPUT:
            if node.get("locked") != LOCKED_PLACEHOLDER or node.get("original") != ORIGINAL_PLACEHOLDER:
                raise PolicyError("private-source-placeholder")
            continue
        for field in ("locked", "original"):
            source = node.get(field)
            keys = {"type", "owner", "repo", "rev", "narHash", "lastModified"} if field == "locked" else {"type", "owner", "repo", "rev", "ref"}
            if not isinstance(source, dict) or set(source) - keys or source.get("type") != "github":
                raise PolicyError("unapproved-flake-source")
            owner, repo = source.get("owner"), source.get("repo")
            if not isinstance(owner, str) or not isinstance(repo, str) or owner + "/" + repo not in allowed:
                raise PolicyError("unapproved-flake-source")
            if any(not isinstance(source[key], str) for key in ("rev", "ref") if key in source):
                raise PolicyError("invalid-flake-lock")
            if field == "locked":
                if (not isinstance(source.get("rev"), str)
                        or not re.fullmatch(r"[0-9a-f]{40}", source["rev"])
                        or not isinstance(source.get("narHash"), str)
                        or not re.fullmatch(r"sha256-[A-Za-z0-9+/]{43}=", source["narHash"])
                        or type(source.get("lastModified", 0)) is not int):
                    raise PolicyError("invalid-flake-lock")

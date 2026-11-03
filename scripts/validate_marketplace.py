#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Optional

from validate_skills import NAME_PATTERN, validate_skill

CATALOG_PATH = Path(".claude-plugin/marketplace.json")
CATALOG_FILE = str(CATALOG_PATH)
EXCLUDED_PATH = Path("scripts/marketplace-excluded.json")
EXCLUDED_FILE = str(EXCLUDED_PATH)
DESCRIPTION_MAX = 200
ROOT_FIELDS = {
    "$schema",
    "name",
    "description",
    "owner",
    "plugins",
    "renames",
    "forceRemoveDeletedPlugins",
}
ENTRY_TEXT_FIELDS = ("name", "description", "category")


class Finding(NamedTuple):
    file: str
    entry: Optional[str]
    message: str

    def __str__(self):
        where = f"{self.file}:{self.entry}" if self.entry else self.file
        return f"{where}: {self.message}"


def is_text(value):
    return isinstance(value, str) and bool(value.strip())


def is_slug(value):
    return isinstance(value, str) and bool(NAME_PATTERN.match(value))


def inside(root, path):
    return path.resolve().is_relative_to(root.resolve())


def check_root(document):
    findings = []
    for field in ("name", "description"):
        if not is_text(document.get(field)):
            findings.append(f"'{field}' is required")
    owner = document.get("owner")
    if not (isinstance(owner, dict) and is_text(owner.get("name"))):
        findings.append("'owner.name' is required")
    findings += [
        f"unknown root field '{key}'" for key in document if key not in ROOT_FIELDS
    ]
    if not isinstance(document.get("plugins"), list):
        findings.append("'plugins' must be a list")
    return findings


def check_skill_paths(root, plugin):
    base = (root / plugin["source"]).resolve()
    skills = plugin.get("skills")
    if not (isinstance(skills, list) and skills and all(isinstance(s, str) for s in skills)):
        return ["'skills' must be a non-empty list when 'strict' is false"]

    findings = []
    for index, relative in enumerate(skills):
        skill_dir = (base / relative).resolve()
        if not inside(root, skill_dir):
            findings.append(f"'skills[{index}]' points outside the repository")
        elif not skill_dir.is_dir():
            findings.append(f"'skills[{index}]' is '{relative}' and it does not exist")
        else:
            findings += [f"'skills[{index}]': {p}" for p in validate_skill(skill_dir)]
    first = (base / skills[0]).resolve()
    if first.name != plugin["name"]:
        findings.append(
            f"'skills[0]' folder is '{first.name}', it must be '{plugin['name']}'"
        )
    return findings


def check_text_limits(plugin):
    name = plugin.get("name")
    description = plugin.get("description")
    findings = []
    if is_text(name) and not is_slug(name):
        findings.append(f"'name' is '{name}', use a lowercase slug with single hyphens")
    if is_text(description) and len(description) > DESCRIPTION_MAX:
        findings.append(
            f"'description' is {len(description)} characters, the limit is {DESCRIPTION_MAX}"
        )
    return findings


def check_fields(plugin):
    missing = [f for f in ENTRY_TEXT_FIELDS if not is_text(plugin.get(f))]
    findings = [f"'{field}' is required" for field in missing]
    findings += [
        f"'{field}' is required" for field in ("source", "strict") if field not in plugin
    ]
    findings += check_text_limits(plugin)
    if "version" in plugin:
        findings.append("'version' is not allowed")
    if "strict" in plugin and plugin["strict"] is not False:
        findings.append("'strict' must be false so the skills paths are validated")
    return findings


def check_source(root, plugin):
    if "source" not in plugin:
        return []
    source = plugin["source"]
    if not (isinstance(source, str) and source.startswith("./")):
        return ["'source' must be a local path starting with './'"]
    if not inside(root, root / source):
        return ["'source' points outside the repository"]
    if plugin.get("strict") is False and is_text(plugin.get("name")):
        return check_skill_paths(root, plugin)
    return []


def check_entry(root, plugin):
    return check_fields(plugin) + check_source(root, plugin)


def check_plugins(root, plugins):
    findings = []
    names = []
    for index, plugin in enumerate(plugins):
        named = isinstance(plugin, dict) and is_text(plugin.get("name"))
        label = plugin["name"] if named else f"plugins[{index}]"
        if not isinstance(plugin, dict):
            findings.append(Finding(CATALOG_FILE, label, "entry must be an object"))
            continue
        if label in names:
            findings.append(Finding(CATALOG_FILE, label, f"duplicate plugin name '{label}'"))
        names.append(label)
        findings += [Finding(CATALOG_FILE, label, m) for m in check_entry(root, plugin)]
    return findings, names


def check_coverage(root, names, excluded):
    skills_dir = root / "skills"
    if not skills_dir.is_dir():
        return []
    return [
        Finding(CATALOG_FILE, folder.name,
                f"skill folder is neither a plugin nor in {EXCLUDED_FILE}")
        for folder in sorted(skills_dir.iterdir())
        if folder.is_dir() and folder.name not in names and folder.name not in excluded
    ]


def read_excluded(root):
    path = root / EXCLUDED_PATH
    if not path.is_file():
        return {}, []
    try:
        excluded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [Finding(EXCLUDED_FILE, None, f"not valid JSON: {exc}")]
    if not isinstance(excluded, dict) or not all(is_text(v) for v in excluded.values()):
        return {}, [Finding(EXCLUDED_FILE, None, "must map each skill folder to a reason")]
    return excluded, []


def check_renames(renames, names):
    if not isinstance(renames, dict):
        return ["'renames' must be an object"]
    findings = []
    for old, new in renames.items():
        if not is_slug(old):
            findings.append(f"'renames' key '{old}' must be a slug")
        elif old in names:
            findings.append(f"'renames' key '{old}' is still a plugin")
        if new is not None and not is_slug(new):
            findings.append(f"'renames' value for '{old}' must be a slug or null")
    return findings


def previous_names(previous):
    plugins = previous.get("plugins") if isinstance(previous, dict) else None
    if not isinstance(plugins, list):
        return []
    return [p["name"] for p in plugins if isinstance(p, dict) and isinstance(p.get("name"), str)]


def check_removed(previous, names, renames):
    return [
        Finding(CATALOG_FILE, old, "removed without an entry in 'renames'")
        for old in previous_names(previous)
        if old not in names and not (isinstance(renames, dict) and old in renames)
    ]


def read_catalog(root):
    path = root / CATALOG_PATH
    if not path.is_file():
        return None, Finding(CATALOG_FILE, None, "file not found")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, Finding(CATALOG_FILE, None, f"not valid JSON: {exc}")
    if not isinstance(document, dict):
        return None, Finding(CATALOG_FILE, None, "root must be an object")
    return document, None


def validate_marketplace(root, previous=None):
    document, finding = read_catalog(root)
    if finding:
        return [finding]

    findings = [Finding(CATALOG_FILE, None, m) for m in check_root(document)]
    plugins = document["plugins"] if isinstance(document.get("plugins"), list) else []
    plugin_findings, names = check_plugins(root, plugins)
    excluded, excluded_findings = read_excluded(root)
    renames = document.get("renames", {})
    findings += plugin_findings
    findings += excluded_findings
    findings += [Finding(CATALOG_FILE, None, m) for m in check_renames(renames, names)]
    findings += check_coverage(root, names, excluded)
    if previous is not None:
        findings += check_removed(previous, names, renames)
    return findings


def load_previous(root, ref):
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{ref}:{CATALOG_PATH.as_posix()}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
    )
    if result.returncode:
        return None, result.stderr.strip()
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"not valid JSON: {exc}"


def main(argv):
    parser = argparse.ArgumentParser(description="Validate the marketplace catalog against the skills folders.")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--previous", metavar="GIT_REF")
    args = parser.parse_args(argv[1:])

    previous = None
    findings = []
    if args.previous:
        previous, error = load_previous(args.root, args.previous)
        if error:
            findings.append(Finding(CATALOG_FILE, None, f"cannot read {args.previous}: {error}"))
    findings += validate_marketplace(args.root, previous)

    for finding in findings:
        print(finding)
    print(f"Checked {count_plugins(args.root)} plugin(s), {len(findings)} problem(s).")
    return 1 if findings else 0


def count_plugins(root):
    try:
        plugins = json.loads((root / CATALOG_PATH).read_text(encoding="utf-8"))["plugins"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return len(plugins) if isinstance(plugins, list) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

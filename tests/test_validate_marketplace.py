"""Tests for scripts/validate_marketplace.py. Run with: python -m unittest discover tests"""

import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).absolute().parent.parent / "scripts"))

from validate_marketplace import Finding, main, validate_marketplace  # noqa: E402

CATALOG = ".claude-plugin/marketplace.json"


def skill_text(name, description="Does a thing. Use when asked."):
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# Body\n"


def entry(name="alpha", **overrides):
    base = {
        "name": name,
        "description": "Short description.",
        "category": "ai-design",
        "source": f"./skills/{name}",
        "strict": False,
        "skills": ["./"],
    }
    base.update(overrides)
    return base


def catalog(*plugins, **extra):
    base = {
        "name": "demo",
        "description": "Demo marketplace.",
        "owner": {"name": "someone"},
        "plugins": list(plugins),
    }
    base.update(extra)
    return base


class MarketplaceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def add_skill(self, name, text=None):
        self.write(f"skills/{name}/SKILL.md", text or skill_text(name))

    def write_catalog(self, document):
        self.write(CATALOG, json.dumps(document))

    def build(self, *names, **extra):
        for name in names:
            self.add_skill(name)
        self.write_catalog(catalog(*(entry(n) for n in names), **extra))

    def messages(self, previous=None):
        return [str(f) for f in validate_marketplace(self.root, previous)]

    def assertMessage(self, fragment, previous=None):
        found = self.messages(previous)
        self.assertTrue(
            any(fragment in m for m in found), f"{fragment!r} not in {found}"
        )

    def assertClean(self, previous=None):
        self.assertEqual(self.messages(previous), [])


class DocumentTest(MarketplaceTestCase):
    def test_valid_catalog_is_clean(self):
        self.build("alpha", "beta")
        self.assertClean()

    def test_missing_file(self):
        self.assertEqual(
            self.messages(), [f"{CATALOG}: file not found"]
        )

    def test_invalid_json(self):
        self.write(CATALOG, "{nope")
        self.assertMessage(f"{CATALOG}: not valid JSON")

    def test_root_must_be_object(self):
        self.write(CATALOG, "[]")
        self.assertMessage("root must be an object")

    def test_root_requires_fields(self):
        self.write_catalog({"plugins": []})
        found = self.messages()
        for field in ("name", "description", "owner.name"):
            self.assertIn(f"{CATALOG}: '{field}' is required", found)

    def test_owner_name_must_be_text(self):
        self.write_catalog(catalog(owner={"name": ""}))
        self.assertMessage("'owner.name' is required")

    def test_owner_must_be_object(self):
        self.write_catalog(catalog(owner="me"))
        self.assertMessage("'owner.name' is required")

    def test_unknown_root_field(self):
        self.build("alpha", surprise=1)
        self.assertEqual(
            self.messages(), [f"{CATALOG}: unknown root field 'surprise'"]
        )

    def test_plugins_must_be_list(self):
        self.write_catalog(catalog(plugins={}))
        self.assertMessage("'plugins' must be a list")

    def test_entry_must_be_object(self):
        self.write_catalog(catalog("alpha"))
        self.assertEqual(
            self.messages(), [f"{CATALOG}:plugins[0]: entry must be an object"]
        )

    def test_known_root_fields_are_accepted(self):
        self.build(
            "alpha",
            renames={},
            forceRemoveDeletedPlugins=True,
            excluded={},
            **{"$schema": "https://example.test/schema.json"},
        )
        self.assertClean()


class EntryTest(MarketplaceTestCase):
    def build_with(self, **overrides):
        self.add_skill("alpha")
        self.write_catalog(catalog(entry("alpha", **overrides)))

    def test_required_fields(self):
        self.add_skill("alpha")
        self.write_catalog(catalog({"skills": ["./"]}))
        found = self.messages()
        for field in ("name", "description", "category", "source", "strict"):
            self.assertIn(f"{CATALOG}:plugins[0]: '{field}' is required", found)

    def test_name_must_be_slug(self):
        self.add_skill("alpha")
        self.write_catalog(catalog(entry("Bad_Name")))
        self.assertMessage("'name' is 'Bad_Name'")

    def test_description_limit_boundary(self):
        self.build_with(description="x" * 200)
        self.assertClean()
        self.build_with(description="x" * 201)
        self.assertEqual(
            self.messages(),
            [f"{CATALOG}:alpha: 'description' is 201 characters, the limit is 200"],
        )

    def test_description_must_be_text(self):
        self.build_with(description=5)
        self.assertMessage("'description' is required")

    def test_blank_description(self):
        self.build_with(description="  ")
        self.assertMessage("'description' is required")

    def test_category_must_be_text(self):
        self.build_with(category="")
        self.assertMessage("'category' is required")

    def test_version_is_forbidden(self):
        self.build_with(version="1.0.0")
        self.assertEqual(
            self.messages(), [f"{CATALOG}:alpha: 'version' is not allowed"]
        )

    def test_strict_must_be_boolean(self):
        self.build_with(strict="no")
        self.assertMessage("'strict' must be true or false")

    def test_source_must_be_local_path(self):
        self.build_with(source="https://example.test/repo.git")
        self.assertMessage("'source' must be a local path starting with './'")

    def test_source_must_stay_inside_repo(self):
        self.build_with(source="./../elsewhere")
        self.assertMessage("'source' points outside the repository")

    def test_duplicate_names(self):
        self.add_skill("alpha")
        self.write_catalog(catalog(entry("alpha"), entry("alpha")))
        self.assertMessage("duplicate plugin name 'alpha'")

    def test_two_entries_are_both_checked(self):
        self.add_skill("alpha")
        self.add_skill("beta")
        self.write_catalog(catalog(entry("alpha"), entry("beta", version="1")))
        self.assertEqual(
            self.messages(), [f"{CATALOG}:beta: 'version' is not allowed"]
        )


class SkillsTest(MarketplaceTestCase):
    def build_with(self, **overrides):
        self.add_skill("alpha")
        self.write_catalog(catalog(entry("alpha", **overrides)))

    def test_strict_false_requires_skills(self):
        self.add_skill("alpha")
        plugin = entry("alpha")
        del plugin["skills"]
        self.write_catalog(catalog(plugin))
        self.assertMessage("'skills' must be a non-empty list when 'strict' is false")

    def test_empty_skills(self):
        self.build_with(skills=[])
        self.assertMessage("'skills' must be a non-empty list")

    def test_skills_must_be_text_paths(self):
        self.build_with(skills=[3])
        self.assertMessage("'skills' must be a non-empty list")

    def test_strict_true_skips_skills_checks(self):
        self.build_with(strict=True, skills=[])
        self.assertClean()

    def test_missing_skill_path(self):
        self.build_with(skills=["./nowhere"])
        self.assertMessage("'./nowhere' and it does not exist")

    def test_missing_skill_file(self):
        self.write("skills/alpha/README.md", "x")
        self.write_catalog(catalog(entry("alpha")))
        self.assertMessage("SKILL.md is missing")

    def test_invalid_frontmatter_is_reported(self):
        self.add_skill("alpha", "no frontmatter")
        self.write_catalog(catalog(entry("alpha")))
        self.assertMessage("frontmatter")

    def test_skill_path_escaping_repo(self):
        self.build_with(skills=["../../.."])
        self.assertMessage("points outside the repository")

    def test_entry_name_must_match_skill_folder(self):
        self.add_skill("alpha")
        self.write_catalog(
            catalog(entry("alpha", source="./", skills=["./skills/alpha"]))
        )
        self.assertClean()
        self.add_skill("beta")
        self.write_catalog(
            catalog(entry("alpha", source="./", skills=["./skills/beta"]))
        )
        self.assertMessage("'skills[0]' folder is 'beta', it must be 'alpha'")

    def test_frontmatter_name_must_match_entry(self):
        self.add_skill("alpha", skill_text("other"))
        self.write_catalog(catalog(entry("alpha")))
        self.assertMessage("'name' is 'other' but the folder is 'alpha'")

    def test_only_first_skill_names_the_plugin(self):
        self.add_skill("alpha")
        self.add_skill("beta")
        self.write_catalog(
            catalog(
                entry("alpha", source="./", skills=["./skills/alpha", "./skills/beta"]),
                excluded={"beta": "bundled with alpha"},
            )
        )
        self.assertClean()


class CoverageTest(MarketplaceTestCase):
    def test_unlisted_skill_folder(self):
        self.build("alpha")
        self.add_skill("gamma")
        self.assertEqual(
            self.messages(),
            [f"{CATALOG}:gamma: skill folder is neither a plugin nor in 'excluded'"],
        )

    def test_excluded_skill_folder_is_allowed(self):
        self.build("alpha", excluded={"gamma": "draft"})
        self.add_skill("gamma")
        self.assertClean()

    def test_excluded_must_be_object_of_reasons(self):
        self.build("alpha", excluded=["gamma"])
        self.assertMessage("'excluded' must map each skill folder to a reason")
        self.build("alpha", excluded={"gamma": ""})
        self.assertMessage("'excluded' must map each skill folder to a reason")

    def test_files_in_skills_dir_are_ignored(self):
        self.build("alpha")
        self.write("skills/NOTES.md", "x")
        self.assertClean()

    def test_missing_skills_dir_is_clean(self):
        self.write_catalog(catalog())
        self.assertClean()


class RenamesTest(MarketplaceTestCase):
    def test_rename_key_must_not_be_a_plugin(self):
        self.build("alpha", renames={"alpha": "beta"})
        self.assertMessage("'renames' key 'alpha' is still a plugin")

    def test_rename_values(self):
        self.build("alpha", renames={"old": "alpha", "gone": None})
        self.assertClean()

    def test_rename_value_must_be_slug_or_null(self):
        self.build("alpha", renames={"old": "Not A Slug"})
        self.assertMessage("'renames' value for 'old' must be a slug or null")
        self.build("alpha", renames={"old": 3})
        self.assertMessage("'renames' value for 'old' must be a slug or null")

    def test_rename_key_must_be_slug(self):
        self.build("alpha", renames={"Bad Key": None})
        self.assertMessage("'renames' key 'Bad Key' must be a slug")

    def test_renames_must_be_object(self):
        self.build("alpha", renames=["old"])
        self.assertMessage("'renames' must be an object")


class PreviousTest(MarketplaceTestCase):
    def previous(self, *names, **extra):
        return catalog(*(entry(n) for n in names), **extra)

    def test_removed_plugin_requires_rename(self):
        self.build("alpha")
        self.assertEqual(
            self.messages(self.previous("alpha", "beta")),
            [f"{CATALOG}:beta: removed without an entry in 'renames'"],
        )

    def test_removed_plugin_with_rename_is_clean(self):
        self.build("alpha", renames={"beta": None})
        self.assertClean(self.previous("alpha", "beta"))

    def test_added_plugin_is_clean(self):
        self.build("alpha", "beta")
        self.assertClean(self.previous("alpha"))

    def test_malformed_previous_is_ignored(self):
        self.build("alpha")
        self.assertClean({"plugins": "oops"})
        self.assertClean({"plugins": ["alpha", {"name": 3}]})
        self.assertClean([])

    def test_previous_is_not_mutated(self):
        self.build("alpha")
        before = self.previous("alpha", "beta")
        snapshot = copy.deepcopy(before)
        self.messages(before)
        self.assertEqual(before, snapshot)


class FindingTest(unittest.TestCase):
    def test_format(self):
        self.assertEqual(str(Finding("f.json", "alpha", "bad")), "f.json:alpha: bad")
        self.assertEqual(str(Finding("f.json", None, "bad")), "f.json: bad")


class CliTest(MarketplaceTestCase):
    def run_main(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["validate_marketplace.py", "--root", str(self.root), *args])
        return code, out.getvalue()

    def git(self, *args):
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
        )

    def test_clean_exit_zero(self):
        self.build("alpha")
        code, out = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(out, "Checked 1 plugin(s), 0 problem(s).\n")

    def test_errors_exit_one_and_print_findings(self):
        self.build("alpha", surprise=1)
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn(f"{CATALOG}: unknown root field 'surprise'\n", out)
        self.assertTrue(out.endswith("Checked 1 plugin(s), 1 problem(s).\n"))

    def test_unreadable_plugin_count_when_catalog_missing(self):
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(
            out, f"{CATALOG}: file not found\nChecked 0 plugin(s), 1 problem(s).\n"
        )

    def test_plugin_count_is_zero_when_plugins_is_not_a_list(self):
        self.write_catalog(catalog(plugins={"alpha": 1}))
        code, out = self.run_main()
        self.assertEqual(code, 1)
        self.assertTrue(out.endswith("Checked 0 plugin(s), 1 problem(s).\n"))

    def test_previous_ref_detects_removal(self):
        self.build("alpha", "beta")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")
        (self.root / "skills/beta/SKILL.md").unlink()
        (self.root / "skills/beta").rmdir()
        self.write_catalog(catalog(entry("alpha")))
        code, out = self.run_main("--previous", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn(f"{CATALOG}:beta: removed without an entry in 'renames'", out)
        self.write_catalog(catalog(entry("alpha"), renames={"beta": None}))
        code, _ = self.run_main("--previous", "HEAD")
        self.assertEqual(code, 0)

    def test_previous_ref_unknown(self):
        self.build("alpha")
        self.git("init", "-q")
        code, out = self.run_main("--previous", "nope")
        self.assertEqual(code, 1)
        self.assertIn("cannot read nope:", out)

    def test_previous_ref_with_invalid_json(self):
        self.write(CATALOG, "{bad")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")
        self.build("alpha")
        code, out = self.run_main("--previous", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn("cannot read HEAD:", out)

    def test_previous_ref_without_catalog_is_an_error(self):
        self.build("alpha")
        self.git("init", "-q")
        self.git("commit", "-q", "--allow-empty", "-m", "empty")
        code, out = self.run_main("--previous", "HEAD")
        self.assertEqual(code, 1)
        self.assertIn("cannot read HEAD:", out)


if __name__ == "__main__":
    unittest.main()

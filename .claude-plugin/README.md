# Claude Code plugin marketplace

This folder publishes the skills in this repository as a Claude Code plugin marketplace named `elayadesign-ai-design-skills`. Each skill is its own plugin, so you install only what you need. The repository root is also a single plugin under the same name, declared by `.claude-plugin/plugin.json`, so the 23people catalog can deliver both skills in one install.

## Install

Needs Claude Code 2.1.275 or later.

```text
/plugin marketplace add 23people-io/elayadesign-ai-design-skills
/plugin install landing-page-design --marketplace 23people-io/elayadesign-ai-design-skills
/plugin install figma-designer --marketplace 23people-io/elayadesign-ai-design-skills
```

Or from the UI: run `/plugins`, open **Marketplaces**, choose **+ Add Marketplace**, enter `https://github.com/23people-io/elayadesign-ai-design-skills`, then pick the plugins to install.

Invoke a skill explicitly with `/<plugin>:<skill>`, for example `/landing-page-design:landing-page-design`.

From the 23people catalog, the repository root installs as one plugin with both skills:

```text
/plugin install elayadesign-ai-design-skills --marketplace 23people-io/23p-skills
```

## Updates

Auto-update is off by default for third-party marketplaces. To turn it on, run `/plugins`, open **Marketplaces**, select this marketplace and choose **Enable auto-update**. Otherwise run `/plugin marketplace update` when you want new versions.

## What you install

The plugins serve the copy of `skills/` in this fork, not upstream directly. Upstream changes reach you only after the fork is synced (**Sync fork** on GitHub) and merged, which is the review point.

## Do not install alongside the 23p marketplaces

If you already use `23p-marketing` or the `elayadesign-ai-design-skills` entry from 23people-io/23p-skills, do not install these plugins as well. The same skills would load twice, and each costs about 174 tokens of always-on context.

## Maintaining

- Validate before opening a PR (needs `pip install pyyaml`): `python scripts/validate_marketplace.py`. In a PR, add `--previous origin/main` to check removals against the base branch (a ref without a catalog is treated as nothing to compare).
- Plugin names are permanent. To rename or remove a plugin, delete its entry and add `"renames": {"<old>": "<new>"}` (or `null` to remove) so installed copies migrate.
- Check how far the fork is behind upstream: `gh api repos/23people-io/elayadesign-ai-design-skills/compare/main...elayadesign:main --jq '.ahead_by'`.
- A skill folder that must not be published (a draft, or one bundled into another plugin) goes in `scripts/marketplace-excluded.json` as `{"<folder>": "<reason>"}`. It lives outside the catalog because `claude plugin validate --strict` rejects unknown root fields.
- `forceRemoveDeletedPlugins` (root field, `true`) makes Claude Code uninstall plugins that disappeared from the catalog without a rename. Set it only when a removed plugin must be taken off users' machines.

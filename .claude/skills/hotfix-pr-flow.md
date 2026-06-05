---
name: hotfix-pr-flow
description: Use when a TraceNex/Fy-api production hotfix must ship independently to main while also syncing the same fix to develop.
---

# Hotfix PR Flow

Use this for Fy-api/TraceNex urgent fixes that should be releasable from `main` without waiting for `develop`.

## Rules

- Do not merge directly into `main`, `master`, or `develop`; all integration goes through PRs.
- Treat this repo's production branch as `main` unless the user explicitly names another existing branch.
- Release targets are branch-bound: `main` may release to production targets `cn` / `sg`; `develop` may release only to test targets `cn-test` / `sg-test`.
- Never release `develop` or any non-`main` branch to `cn` / `sg` unless the user gives an explicit second confirmation for that exact command.
- Before any `fab release`, state and verify the exact `--target`, `--tag`, and `--ref` command; do not infer a production target from "release develop".
- Preserve unrelated user changes. If the hotfix was created on `develop`, stash or patch it before switching to `origin/main`.
- The `develop` PR must be a cherry-pick of the production hotfix commit, not a separate reimplementation.

## Workflow

1. Inspect state:
   - `git status --short --branch`
   - `git fetch origin`
2. Save dirty hotfix changes if needed:
   - `git stash push -u -m "hotfix <topic>"`
3. Create production PR branch from latest `origin/main`:
   - `git switch -c hotfix/<topic> origin/main`
   - apply the stash or patch
   - run targeted tests
   - `git add <files>`
   - `git commit -m "fix: <topic>"`
   - `git push -u origin hotfix/<topic>`
   - `gh pr create --base main --head hotfix/<topic> ...`
4. Create develop PR branch from latest `origin/develop`:
   - `git switch -c hotfix/<topic>-develop origin/develop`
   - `git cherry-pick <production-hotfix-commit>`
   - run the same targeted tests
   - `git push -u origin hotfix/<topic>-develop`
   - `gh pr create --base develop --head hotfix/<topic>-develop ...`
5. Report both PR links, commit hashes, test commands, and current branch.

## Release Mapping

- Production: `fab release --target=cn|sg --ref=origin/main ...`
- Test: `fab release --target=cn-test|sg-test --ref=origin/develop ...`

## Verification

Run at least the targeted regression test on both branches. Use `-count=1` for the final verification when Go test caching could hide a problem.

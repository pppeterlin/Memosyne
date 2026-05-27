#!/usr/bin/env bash
# Memosyne · The Rite of Release
#
# One command to bump version, tag, and prepare GitHub release notes.
# Designed to make tagging boring and routine so we never "forget v0.5"
# again.
#
# Flow:
#   1. Pre-flight: clean working tree, on master, up-to-date with origin
#   2. Run `make verify` — no green, no tag
#   3. Bump pyproject.toml version (writes the literal string in-place)
#   4. Commit "Bump version to X.Y.Z"
#   5. Tag vX.Y.Z (annotated)
#   6. Generate release-notes draft from git log into /tmp/release_X.Y.Z.md
#   7. Show user the next-step commands (push + gh release create)
#
# Usage:
#   scripts/release.sh 0.7.0
#   scripts/release.sh 0.7.0 --dry-run   # preflight + show plan, no writes
#
# Why bash not python: zero deps, transparent, easy to audit. Anyone with
# basic shell can read this and trust what it does to their git history.

set -euo pipefail

# ── Args ────────────────────────────────────────────────────
if [[ $# -lt 1 ]]; then
  echo "usage: $0 X.Y.Z [--dry-run]" >&2
  exit 2
fi
VERSION="$1"
DRY_RUN=0
if [[ "${2:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

# Validate semver-ish (allows X.Y.Z and X.Y.Z-suffix)
if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
  echo "✗ version must look like X.Y.Z (got: $VERSION)" >&2
  exit 2
fi

TAG="v$VERSION"
NOTES_PATH="/tmp/release_${VERSION}.md"

# ── Pre-flight ──────────────────────────────────────────────
preflight() {
  echo "── Pre-flight checks ──"

  # On master
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD)
  if [[ "$branch" != "master" && "$branch" != "main" ]]; then
    echo "✗ must be on master/main, got: $branch" >&2
    exit 1
  fi
  echo "  ✓ on $branch"

  # Clean working tree (allow submodule dirty since _vault is private)
  if ! git diff --quiet --ignore-submodules || ! git diff --cached --quiet --ignore-submodules; then
    echo "✗ working tree dirty; commit or stash first" >&2
    git status --short --ignore-submodules >&2
    exit 1
  fi
  echo "  ✓ working tree clean (submodules ignored)"

  # Up to date with origin
  git fetch origin "$branch" --quiet
  local local_sha remote_sha
  local_sha=$(git rev-parse HEAD)
  remote_sha=$(git rev-parse "origin/$branch")
  if [[ "$local_sha" != "$remote_sha" ]]; then
    echo "✗ local $branch differs from origin/$branch; pull/push first" >&2
    exit 1
  fi
  echo "  ✓ in sync with origin/$branch"

  # Tag doesn't already exist
  if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "✗ tag $TAG already exists" >&2
    exit 1
  fi
  echo "  ✓ tag $TAG does not exist yet"

  # pyproject.toml present and parseable
  if [[ ! -f pyproject.toml ]]; then
    echo "✗ pyproject.toml not found" >&2
    exit 1
  fi
  local current
  current=$(grep -E '^version = ' pyproject.toml | head -1 | sed -E 's/version = "([^"]+)"/\1/')
  echo "  ✓ pyproject.toml current version: $current"
  if [[ "$current" == "$VERSION" ]]; then
    echo "✗ pyproject.toml is already at $VERSION; nothing to bump" >&2
    exit 1
  fi
}

# ── make verify ─────────────────────────────────────────────
run_verify() {
  echo ""
  echo "── make verify ──"
  if ! make verify >/dev/null 2>&1; then
    echo "✗ make verify failed; run it directly to see details" >&2
    echo "  no green, no tag" >&2
    exit 1
  fi
  echo "  ✓ all invariants + unit tests pass"
}

# ── Bump version ────────────────────────────────────────────
bump_version() {
  echo ""
  echo "── Bump pyproject.toml ──"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  (dry-run) would set version = \"$VERSION\""
    return
  fi
  # sed -i has different syntax on macOS vs Linux; use a temp file for portability
  local tmp
  tmp=$(mktemp)
  sed -E "s/^version = \"[^\"]+\"/version = \"$VERSION\"/" pyproject.toml > "$tmp"
  mv "$tmp" pyproject.toml
  echo "  ✓ pyproject.toml -> $VERSION"
}

# ── Commit + tag ────────────────────────────────────────────
commit_and_tag() {
  echo ""
  echo "── Commit + tag ──"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  (dry-run) would commit and tag $TAG"
    return
  fi
  git add pyproject.toml
  git commit -m "Bump version to $VERSION"
  local commit_sha
  commit_sha=$(git rev-parse HEAD)
  git tag -a "$TAG" -m "Memosyne $TAG" "$commit_sha"
  echo "  ✓ commit + annotated tag $TAG at $commit_sha"
}

# ── Generate release notes draft ────────────────────────────
generate_notes() {
  echo ""
  echo "── Release notes draft ──"
  # Find the previous tag (closest annotated)
  local prev_tag
  prev_tag=$(git describe --tags --abbrev=0 "$TAG^" 2>/dev/null || echo "")

  if [[ -z "$prev_tag" ]]; then
    echo "  (no prior tag found; showing all commits)"
    prev_tag="--root"
    range="HEAD"
  else
    echo "  range: $prev_tag..HEAD"
    range="$prev_tag..HEAD"
  fi

  {
    echo "## Summary"
    echo ""
    echo "<one-paragraph release pitch — what changed and why it matters>"
    echo ""
    echo "## Highlights"
    echo ""
    echo "<group commits into themes; pull from CHANGELOG if maintained>"
    echo ""
    echo "## Commits since $prev_tag"
    echo ""
    git log --pretty=format:"- %s (%h)" "$range" | grep -v "^- Merge "
    echo ""
    echo ""
    echo "## Upgrade"
    echo ""
    echo '```bash'
    echo "pip install -e ."
    echo "memosyne health"
    echo '```'
  } > "$NOTES_PATH"

  echo "  ✓ draft written: $NOTES_PATH"
}

# ── Next-step hints ─────────────────────────────────────────
print_next_steps() {
  echo ""
  echo "════════════════════════════════════════"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo "🜍 Dry-run complete. No writes were performed."
    echo ""
    echo "To execute for real:"
    echo "  scripts/release.sh $VERSION"
  else
    echo "🜍 The Rite of Release is prepared."
    echo ""
    echo "Review the draft:"
    echo "  \$EDITOR $NOTES_PATH"
    echo ""
    echo "Push the commit + tag:"
    echo "  git push origin $(git rev-parse --abbrev-ref HEAD) $TAG"
    echo ""
    echo "Publish the GitHub release:"
    echo "  gh release create $TAG --title \"Memosyne $TAG\" --notes-file $NOTES_PATH"
  fi
  echo "════════════════════════════════════════"
}

# ── Main ────────────────────────────────────────────────────
preflight
run_verify
bump_version
commit_and_tag
generate_notes
print_next_steps

#!/bin/sh
# Resolve the `repo` parameter to a directory on disk and print that path.
#   - an existing local directory is used in place, untouched
#   - anything else is treated as a git URL and shallow-cloned into a temp dir
# Prints the absolute path on stdout. Nothing else goes to stdout.
set -eu

repo=$(printf '%s' "${1:-}" | tr -d '\n\r')
branch=$(printf '%s' "${2:-}" | tr -d '\n\r')

if [ -z "$repo" ]; then
  echo "resolve: 'repo' is required (a git URL or a local path)" >&2
  exit 1
fi

if [ -d "$repo" ]; then
  # Local checkout: report it and change nothing.
  printf '%s' "$(cd "$repo" && pwd)"
  exit 0
fi

# Only fetch over transports we intend to support. This is the trust boundary:
# everything after it is repository-authored content, so refuse anything odd
# rather than handing an arbitrary string to git.
case "$repo" in
  https://*|http://*|git@*:*|ssh://*) ;;
  *)
    echo "resolve: not a local directory, and not a git URL I will fetch: $repo" >&2
    echo "resolve: expected https://, ssh://, or git@host:owner/name" >&2
    exit 1
    ;;
esac

dest=$(mktemp -d "${TMPDIR:-/tmp}/repo-brief.XXXXXX")

# --depth 1 keeps this cheap; the brief reports that history was not fetched
# rather than pretending the clone is the whole project.
if [ -n "$branch" ]; then
  git clone --depth 1 --quiet --branch "$branch" -- "$repo" "$dest/src" >&2
else
  git clone --depth 1 --quiet -- "$repo" "$dest/src" >&2
fi

printf '%s' "$dest/src"

#!/usr/bin/env bash
# CI check: runs the commit-msg hook over every non-merge commit in
# <base>..<head>, catching commits made with --no-verify or on GitHub.
set -euo pipefail

base="${1:?Usage: check-range.sh <base> <head>}"
head="${2:?Usage: check-range.sh <base> <head>}"
hook="$(dirname "$0")/conventional.sh"

message_file=$(mktemp)
trap 'rm -f "$message_file"' EXIT

failed=false

for commit in $(git rev-list --no-merges --reverse "${base}..${head}"); do
    short=$(git rev-parse --short "$commit")
    git log -1 --format=%B "$commit" >"$message_file"

    if ! output=$(bash "$hook" "$message_file" 2>&1); then
        failed=true
        echo "${short}: ${output}" >&2
        continue
    fi

    if [ -n "$output" ]; then
        echo "${short}: ${output}" >&2
    fi
done

if [ "$failed" = true ]; then
    exit 1
fi

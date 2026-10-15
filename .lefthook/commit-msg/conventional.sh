#!/usr/bin/env bash
# commit-msg hook: enforces the Conventional Commits rules listed under
# "Commit messages" in README.md that can be checked mechanically. Subject
# problems fail the commit; long body lines only warn.
set -euo pipefail

message_file="${1:?Usage: conventional.sh <commit-message-file>}"

types='feat|fix|refactor|perf|docs|test|chore|build|ci|style|revert'
subject_pattern="^(${types})(\\([a-z0-9][a-z0-9._/-]*\\))?!?: [^[:space:]].*\$"
passthrough_pattern='^(Merge |Revert "|fixup! |squash! |amend! )'
scissors_pattern='^# -+ >8 -+$'
subject_limit=72
body_limit=72

fail() {
    echo "commit-msg: $1" >&2
    echo "  Expected: <type>(<scope>)!: <imperative summary>" >&2
    echo "  Types: ${types//|/, }" >&2
    exit 1
}

subject=''
line_number=0
subject_followed_by_text=false
long_body_lines=()

while IFS= read -r line || [ -n "$line" ]; do
    if [[ $line =~ $scissors_pattern ]]; then
        break
    fi

    if [[ $line == '#'* ]]; then
        continue
    fi

    line_number=$((line_number + 1))

    if [ "$line_number" -eq 1 ]; then
        subject="$line"
        continue
    fi

    if [ "$line_number" -eq 2 ]; then
        if [ -n "$line" ]; then
            subject_followed_by_text=true
        fi

        continue
    fi

    if [ "${#line}" -gt "$body_limit" ] && [[ $line != *://* ]]; then
        long_body_lines+=("$line")
    fi
done <"$message_file"

if [ -z "$subject" ]; then
    fail 'empty commit message.'
fi

if [[ $subject =~ $passthrough_pattern ]]; then
    exit 0
fi

if ! [[ $subject =~ $subject_pattern ]]; then
    fail "subject is not in Conventional Commits format: \"$subject\""
fi

if [ "${#subject}" -gt "$subject_limit" ]; then
    fail "subject is ${#subject} characters; the hard cap is ${subject_limit}."
fi

if [[ $subject == *. ]]; then
    fail 'subject must not end with a full stop.'
fi

if [ "$subject_followed_by_text" = true ]; then
    fail 'leave a blank line between the subject and the body.'
fi

if [ "${#long_body_lines[@]}" -gt 0 ]; then
    echo "commit-msg: warning: ${#long_body_lines[@]} body line(s) exceed ${body_limit} characters." >&2
fi

exit 0

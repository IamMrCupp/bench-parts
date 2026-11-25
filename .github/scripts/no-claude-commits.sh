#!/usr/bin/env bash
# Fails if any commit in range has Claude as author or committer, or a Claude
# trailer in its message. Claude reviewing PRs is fine; Claude in history isn't.
#
#   BASE set  -> checks BASE..HEAD (a pull request's own commits)
#   BASE empty -> checks everything reachable from HEAD (a push to main)
set -euo pipefail

head="${HEAD:-HEAD}"
if [ -n "${BASE:-}" ]; then range="$BASE..$head"; else range="$head"; fi

# Strict on purpose: no Claude in commits, in any form. The one trade-off is a
# real person whose name starts with "Claude" (Jean-Claude, Claudette pass).
#
# Identity, author or committer: a name starting with the word Claude (Claude,
# Claude Code, Claude Opus, claude[bot]), any claude[bot] GitHub noreply
# address, or any anthropic.com address.
ident_re='^"?claude([^[:alnum:]]|$)|claude\[bot\]@users\.noreply\.github\.com>$|anthropic\.com>$'
# Message body only, never the subject, so a subject like "docs: Claude may
# review PRs" is fine:
# - any "...-by:" trailer (Co-authored-by, Signed-off-by, Assisted-by,
#   Generated-by, Reviewed-by, ...) naming Claude, a claude[bot] address or an
#   anthropic.com address;
# - any "Generated with/by/using Claude" line, however it ends.
trailer_re='^[[:space:]]*[[:alpha:]]+(-[[:alpha:]]+)*-by:[[:space:]]*("?claude([^[:alnum:]]|$)|.*claude\[bot\]@|.*anthropic\.com)'
footer_re='^[^[:alnum:]]*generated (with|by|using) \[?claude([^[:alnum:]]|$)'

# Resolved up front so a bad range fails the job instead of checking nothing.
commits="$(git rev-list "$range")"

bad=0
checked=0
while read -r c; do
  [ -n "$c" ] || continue
  checked=$((checked + 1))
  # Read first, so a git failure fails the job instead of reading as clean.
  ident="$(git log -1 --format='%an <%ae>%n%cn <%ce>' "$c")"
  # Body only: everything after the subject line. CRs stripped so a CRLF
  # message can't hide a line ending.
  msg="$(git log -1 --format='%b' "$c" | tr -d '\r')"
  hits="$(
    {
      grep -iE "$ident_re" <<< "$ident" || true
      grep -iE "$trailer_re|$footer_re" <<< "$msg" || true
    }
  )"
  if [ -n "$hits" ]; then
    bad=$((bad + 1))
    echo "::error::$(git rev-parse --short "$c") $(git log -1 --format='%s' "$c")"
    # Indent every line, so commit text can't start with a ::workflow-command::.
    while IFS= read -r line; do printf '    %s\n' "$line"; done <<< "$hits"
  fi
done <<< "$commits"

if [ "$bad" -gt 0 ]; then
  echo
  echo "$bad of $checked commits carry Claude attribution."
  echo "Re-author them to the maintainer and drop the trailers, e.g.:"
  echo "  git rebase -x 'git commit --amend --no-edit --reset-author -s' <base>"
  echo "then remove the Co-Authored-By / Signed-off-by lines with 'git rebase -i'."
  exit 1
fi
echo "$checked commits checked, none carry Claude attribution."

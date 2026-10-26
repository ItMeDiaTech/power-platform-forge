#!/usr/bin/env bash
# Rejects a commit when any staged file contains credential-shaped content.
# Wired as a pre-commit hook via: git config core.hooksPath .githooks
set -u

pattern='(-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|[Bb]earer [A-Za-z0-9._~+/-]{20,}=*|(client_?secret|clientSecret|password|passwd|AccountKey|SharedAccessSignature)[[:space:]]*[=:][[:space:]]*["'\'']?[A-Za-z0-9~._+/=-]{12,}|Server=tcp:[^;]+;.*Password=)'

status=0
while IFS= read -r -d '' file; do
  # The scanner's own pattern list would match itself.
  [ "$file" = "scripts/check-secrets.sh" ] && continue
  if git show ":$file" | grep -Eaq "$pattern"; then
    echo "check-secrets: credential-shaped content in $file; remove it before committing" >&2
    status=1
  fi
done < <(git diff --cached --name-only --diff-filter=ACM -z)

exit $status

#!/usr/bin/env bash
# Rejects a commit when a staged file contains credentials or tenant identifiers.
# The repo is public, so real SharePoint hosts, Dataverse org URLs, tenant domains,
# UPNs and GUIDs must never land in it; documentation uses placeholders such as contoso.
# Wired as a pre-commit hook via: git config core.hooksPath .githooks
set -u

secret='(-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|[Bb]earer [A-Za-z0-9._~+/-]{20,}=*|(client_?secret|clientSecret|password|passwd|AccountKey|SharedAccessSignature)[[:space:]]*[=:][[:space:]]*["'\'']?[A-Za-z0-9~._+/=-]{12,}|Server=tcp:[^;]+;.*Password=|[?&]sig=[A-Za-z0-9%/+=]{20,})'

# Placeholder names that may appear in docs and examples.
ph='contoso|fabrikam|northwind|adventureworks|example|tenant|yourtenant|your-tenant|yourorg|org|myorg|orgname|placeholder'

# kind <TAB> identifier pattern <TAB> full-match allow pattern
checks="SharePoint host	[A-Za-z0-9-]+\.sharepoint\.(com|us|de|cn)	(${ph})(-my|-admin)?\.sharepoint\.[a-z]+
Dataverse org URL	([A-Za-z0-9-]+\.)+crm[0-9]*\.dynamics\.com	(${ph}|globaldisco)\.(api\.)?crm[0-9]*\.dynamics\.com
tenant domain	[A-Za-z0-9-]+\.onmicrosoft\.com	(${ph})\.onmicrosoft\.com
email or UPN	[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}	[^@]+@((${ph})\.(com|org|net)|(${ph})\.onmicrosoft\.com|users\.noreply\.github\.com|github\.com)
GUID	[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}	(0{8}-0{4}-0{4}-0{4}-0{12}|0000000[0-9a-f]-0000-0000-c000-000000000000|00000003-0000-0ff1-ce00-000000000000|04b07795-8ddb-461a-bbee-02f9e1bf7b46)"
# Public Microsoft first-party ids (resource apps, the Azure CLI client) may be added to the
# GUID allow pattern above; never add an id that belongs to a tenant, user or item.

status=0
while IFS= read -r -d '' file; do
  # The scanner's own pattern lists would match themselves.
  [ "$file" = "scripts/check-secrets.sh" ] && continue
  content=$(git show ":$file")
  if printf '%s' "$content" | grep -Eaq "$secret"; then
    echo "check-secrets: credential-shaped content in $file; remove it before committing" >&2
    status=1
  fi
  while IFS=$'\t' read -r kind pattern allow; do
    # Line numbers only: the identifier itself is not echoed into terminals or logs.
    lines=$(printf '%s' "$content" | grep -naEo "$pattern" | grep -viE "^[0-9]+:(${allow})\$" | cut -d: -f1 | sort -un | tr '\n' ' ')
    if [ -n "$lines" ]; then
      echo "check-secrets: $kind in $file on line(s) ${lines% }; replace it with a placeholder such as contoso" >&2
      status=1
    fi
  done <<< "$checks"
done < <(git diff --cached --name-only --diff-filter=ACMR -z)

exit $status

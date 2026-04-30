#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Resolves the includeAuthenticatorApp flag using the same priority as Gradle.
# Used by .gitconfig droid* aliases to decide whether to include authenticator/.
#
# Priority (highest wins):
#   1. ~/.gradle/gradle.properties  (per-user global)
#   2. gradle.properties            (committed project default)
#   3. local.properties             (per-checkout, gitignored — fallback only)
#
# Exit code:  0 = include authenticator,  1 = skip authenticator
# Usage:      if "$workDir/scripts/resolve-include-authenticator.sh" "$workDir"; then ...
# ---------------------------------------------------------------------------

workDir="${1:-.}"

resolve_flag() {
    local file="$1"
    if [ -f "$file" ]; then
        # Look for the property (ignoring leading/trailing whitespace)
        local val
        val=$(grep -m1 '^[[:space:]]*includeAuthenticatorApp[[:space:]]*=' "$file" 2>/dev/null \
              | sed 's/^[^=]*=[[:space:]]*//' | tr -d '[:space:]')
        if [ -n "$val" ]; then
            echo "$val"
            return 0
        fi
    fi
    return 1
}

# Check in priority order — first match wins
result=$(resolve_flag "$HOME/.gradle/gradle.properties") \
    || result=$(resolve_flag "$workDir/gradle.properties") \
    || result=$(resolve_flag "$workDir/local.properties") \
    || result="false"

[ "$result" = "true" ]

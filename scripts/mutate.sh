#!/usr/bin/env bash
# Usage:
#   ./scripts/mutate.sh              # Full run
#   ./scripts/mutate.sh status.py    # Single module (fnmatch pattern on module path)
#   ./scripts/mutate.sh --results    # Show surviving mutants
set -euo pipefail

if [[ "${1:-}" == "--results" ]]; then
    uv run mutmut results
    exit 0
fi

args=(run)

if [[ -n "${1:-}" ]]; then
    # Convert file shorthand (e.g. "status.py") to mutmut fnmatch pattern (e.g. "hive.status.*")
    # Strip .py suffix and path prefix, convert / to .
    module="${1%.py}"
    module="${module##*/}"
    # Check if it's a subpackage path like db/core.py
    if [[ "$1" == */* ]]; then
        module="${1%.py}"
        module="${module//\//.}"
    fi
    args+=("hive.${module}.*")
fi

uv run mutmut "${args[@]}"

echo ""
echo "=== Summary ==="
uv run mutmut results 2>/dev/null | tail -5

#!/usr/bin/env bash
# Print the addresses written by cilium.sh. Missing file means the cluster
# is not up yet, which is normal during the first plan.
set -euo pipefail

file="${1:-}"
node_ip=""
gateway=""

if [[ -n "$file" && -f "$file" ]]; then
  # shellcheck disable=SC1090
  source "$file"
fi

printf '{"node_ip":"%s","gateway":"%s"}\n' "$node_ip" "$gateway"

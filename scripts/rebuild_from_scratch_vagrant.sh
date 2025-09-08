#!/usr/bin/env bash
# Hard reset of the Vagrant environment + health verification.
# Destroys the VMs, recreates, and verifies /health.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

read -r -p "This will 'vagrant destroy -f' all 3 VMs. Continue? [y/N] " yn
case "${yn,,}" in
  y|yes) ;;
  *) echo "Aborted."; exit 1;;
esac

vagrant destroy -f
vagrant up

# Reuse the logic from build_project.sh to find base + verify
"$ROOT/build_project.sh"

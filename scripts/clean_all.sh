#!/usr/bin/env bash
set -euo pipefail

# Wipe DB rows (tables intact)
scripts/db_wipe_data.sh

# Remove per-user local state keys/configs for fast test resets
for HOME_DIR in "${HOME}" "${HOME}/liliumshare/_userA_home" "${HOME}/liliumshare/_userB_home"; do
  if [ -d "${HOME_DIR}/.liliumshare" ]; then
    echo "Removing ${HOME_DIR}/.liliumshare"
    rm -rf "${HOME_DIR}/.liliumshare"
  fi
done

echo "Clean complete."

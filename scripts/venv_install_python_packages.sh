#!/usr/bin/env bash
# from the project root
deactivate 2>/dev/null || true
rm -rf venv
# make sure venv support is installed
sudo apt update && sudo apt install -y python3.12-venv
python3 -m venv venv
# make sure venv sees outside packages
python3 -m venv --system-site-packages venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r frontend/requirements.txt
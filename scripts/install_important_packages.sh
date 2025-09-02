#!/usr/bin/env bash
# important packages needed for this to work on linux
sudo apt update
sudo apt install -y \
  gstreamer1.0-pipewire gstreamer1.0-tools \
  gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav \
  python3-opencv \
  python3-gi gir1.2-gstreamer-1.0 \
  xdg-desktop-portal xdg-desktop-portal-gnome

# Start these services so the app works
systemctl --user daemon-reload
systemctl --user restart pipewire.service
systemctl --user restart wireplumber.service
systemctl --user restart xdg-desktop-portal.service
systemctl --user restart xdg-desktop-portal-gnome.service 2>/dev/null || true

# Do you have the pipewire GStreamer source?
gst-inspect-1.0 pipewiresrc | head -n 5

# If it says "No such element" install:
#   sudo apt install gstreamer1.0-pipewire gstreamer1.0-plugins-base gstreamer1.0-plugins-good

# Make sure portal services are fresh
systemctl --user restart xdg-desktop-portal.service xdg-desktop-portal-gnome.service


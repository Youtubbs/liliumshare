#!/usr/bin/env bash
# Sanity check to see if you are in headless mode or not in your given environment
echo "DISPLAY=$DISPLAY" 
echo "WAYLAND_DISPLAY=$WAYLAND_DISPLAY" 
echo "XDG_SESSION_TYPE=$XDG_SESSION_TYPE" 

# Here's my output; should be something similar probably. works in x11 too so should be fine in most debian-based distros.
# DISPLAY=:0 
# WAYLAND_DISPLAY=wayland-0 
# XDG_SESSION_TYPE=wayland

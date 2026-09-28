#!/bin/bash
# setup-autostart.sh — (re)install the tunnel autostart into system locations.
# System files outside ~ are ephemeral across VM replacements, so re-run this
# script after a replacement to restore autostart. Everything it needs lives in
# ~/workspace/rp/ (persistent).
set -e
RP=/home/hatch/workspace/rp

chmod 755 $RP/rp-boot.sh
chmod 600 $RP/.token
cp $RP/rp-tunnel.service /etc/systemd/system/rp-tunnel.service
systemctl daemon-reload
systemctl enable rp-tunnel.service
systemctl enable ssh
echo "autostart installed: rp-tunnel + ssh enabled"
systemctl is-enabled rp-tunnel.service ssh

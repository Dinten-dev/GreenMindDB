#!/usr/bin/env bash
# Adds emergency memory backing on the root disk without restarting services.
set -euo pipefail
[[ $(id -u) == 0 ]] || { echo 'Bitte mit sudo ausführen.'; exit 1; }
file=/var/swap-greenmind-visualization
if ! swapon --noheadings --show=NAME | awk '{print $1}' | grep -Fxq "$file"; then
  [[ ! -e "$file" ]] || { echo 'Vorhandene unbekannte Swap-Datei: Abbruch.'; exit 1; }
  free_bytes=$(df -B1 --output=avail /var | tail -1 | tr -d ' ')
  (( free_bytes >= 12884901888 )) || { echo 'Weniger als 12 GiB frei: Abbruch.'; exit 1; }
  umask 077
  fallocate -l 4G "$file"
  chmod 600 "$file"
  mkswap "$file" >/dev/null
  swapon --priority 10 "$file"
fi
if ! grep -Fq "$file " /etc/fstab; then
  cp -a /etc/fstab "/etc/fstab.greenmind-before-$(date -u +%Y%m%dT%H%M%SZ)"
  printf '%s none swap sw,pri=10 0 0\n' "$file" >> /etc/fstab
fi
swapon --show
free -m
echo 'Speicherreserve eingerichtet; keine Anwendungsdienste neu gestartet.'

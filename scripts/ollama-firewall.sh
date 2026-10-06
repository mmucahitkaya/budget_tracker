#!/usr/bin/env bash
# macOS: allow Ollama (port 11434) only from Home Assistant and this Mac.
# The macOS application firewall cannot filter by IP, so this uses pf.
#   Install:    sudo HA_IP=192.168.1.2 scripts/ollama-firewall.sh install
#   Uninstall:  sudo scripts/ollama-firewall.sh uninstall
#   Status:     sudo scripts/ollama-firewall.sh status
set -euo pipefail
HA_IP="${HA_IP:?set HA_IP to your Home Assistant IP address}"
ANCHOR=/etc/pf.anchors/budgettracker.ollama
DAEMON=/Library/LaunchDaemons/com.budgettracker.pf.plist
MARK="# budgettracker-ollama"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

case "${1:-}" in
install)
  cat > "$ANCHOR" <<RULES
# Ollama (11434): only Home Assistant and this Mac
pass in quick on lo0 proto tcp from any to any port 11434
pass in quick proto tcp from $HA_IP to any port 11434
block drop in quick proto tcp from any to any port 11434
RULES
  if ! grep -q "$MARK" /etc/pf.conf; then
    cp /etc/pf.conf /etc/pf.conf.budgettracker-backup
    printf '\n%s\nanchor "budgettracker.ollama"\nload anchor "budgettracker.ollama" from "%s"\n' "$MARK" "$ANCHOR" >> /etc/pf.conf
  fi
  # Enable pf with these rules at boot
  cat > "$DAEMON" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.budgettracker.pf</string>
  <key>ProgramArguments</key><array><string>/sbin/pfctl</string><string>-E</string><string>-f</string><string>/etc/pf.conf</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
PLIST
  pfctl -nf /etc/pf.conf          # syntax check
  pfctl -E -f /etc/pf.conf 2>/dev/null || true
  launchctl bootstrap system "$DAEMON" 2>/dev/null || true
  echo "✓ Installed. Rules:"; pfctl -a budgettracker.ollama -sr
  ;;
uninstall)
  if [ -f /etc/pf.conf.budgettracker-backup ]; then cp /etc/pf.conf.budgettracker-backup /etc/pf.conf; rm /etc/pf.conf.budgettracker-backup; fi
  launchctl bootout system "$DAEMON" 2>/dev/null || true
  rm -f "$DAEMON" "$ANCHOR"
  pfctl -f /etc/pf.conf 2>/dev/null || true
  echo "✓ Removed; Ollama is reachable from the whole network again."
  ;;
status)
  pfctl -s info | head -2; pfctl -a budgettracker.ollama -sr
  ;;
*) echo "usage: sudo $0 install|uninstall|status"; exit 1 ;;
esac

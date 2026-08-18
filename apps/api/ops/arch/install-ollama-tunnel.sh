#!/usr/bin/env bash
set -euo pipefail

key_path=/home/binc/.ssh/ashiraai_ollama_tunnel_ed25519
known_hosts_path=/home/binc/.ssh/ashiraai_ollama_known_hosts
unit_source=${1:-./ashiraai-ollama-tunnel.service}

test -f "$key_path"
test -f "$known_hosts_path"
test -f "$unit_source"
chmod 600 "$key_path"
chmod 600 "$known_hosts_path"
ollama show qwen3:8b >/dev/null

sudo install -o root -g root -m 0644 "$unit_source" /etc/systemd/system/ashiraai-ollama-tunnel.service
sudo systemctl daemon-reload
sudo systemctl enable --now ollama.service ashiraai-ollama-tunnel.service
sudo systemctl --no-pager --full status ashiraai-ollama-tunnel.service

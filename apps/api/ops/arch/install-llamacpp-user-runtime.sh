#!/usr/bin/env bash
set -euo pipefail

ops_dir=${1:-.}
unit_dir=/home/binc/.config/systemd/user

install -d -m 0755 "${unit_dir}"
install -m 0644 "${ops_dir}/ashiraai-llamacpp.user.service" \
    "${unit_dir}/ashiraai-llamacpp.service"
install -m 0644 "${ops_dir}/ashiraai-llamacpp-tunnel.user.service" \
    "${unit_dir}/ashiraai-llamacpp-tunnel.service"
loginctl enable-linger binc
systemctl --user daemon-reload
systemctl --user enable --now ashiraai-llamacpp.service ashiraai-llamacpp-tunnel.service
systemctl --user --no-pager --full status \
    ashiraai-llamacpp.service ashiraai-llamacpp-tunnel.service

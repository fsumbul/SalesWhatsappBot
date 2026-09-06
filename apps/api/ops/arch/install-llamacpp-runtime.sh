#!/usr/bin/env bash
set -euo pipefail

release=b10612
install_root=/home/binc/.local/opt/llama.cpp-${release}
archive=/home/binc/.local/opt/llama-${release}-vulkan.tar.gz
release_url="https://github.com/ggml-org/llama.cpp/releases/download/${release}/llama-${release}-bin-ubuntu-vulkan-x64.tar.gz"
ops_dir=${1:-.}

install -d -m 0755 /home/binc/.local/opt /home/binc/.cache/llama.cpp
if [[ ! -x "${install_root}/llama-server" ]]; then
    curl --fail --location --retry 3 --output "${archive}" "${release_url}"
    install -d -m 0755 "${install_root}"
    tar -xzf "${archive}" -C "${install_root}" --strip-components=1
fi

"${install_root}/llama-server" --version
sudo install -o root -g root -m 0644 \
    "${ops_dir}/ashiraai-llamacpp.service" \
    /etc/systemd/system/ashiraai-llamacpp.service
sudo install -o root -g root -m 0644 \
    "${ops_dir}/ashiraai-llamacpp-tunnel.service" \
    /etc/systemd/system/ashiraai-llamacpp-tunnel.service
sudo systemctl daemon-reload
sudo systemctl enable --now ashiraai-llamacpp.service ashiraai-llamacpp-tunnel.service
sudo systemctl --no-pager --full status ashiraai-llamacpp.service ashiraai-llamacpp-tunnel.service

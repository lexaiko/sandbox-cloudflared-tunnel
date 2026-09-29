#!/bin/bash
# setup-autostart.sh — (re)install and restore the tunnel & ssh into system locations.
# System files outside ~ are ephemeral across VM replacements. Everything needed
# lives in ~/workspace/rp/ (persistent).
set -e
RP=/home/hatch/workspace/rp

chmod 755 $RP/rp-boot.sh
chmod 600 $RP/.token 2>/dev/null || true
chmod 600 $RP/.root_shadow 2>/dev/null || true

# Refresh .proxy-env dari env shell saat ini jika tersedia.
# Password proxy bisa rotate antar sesi (yang lama sejauh ini tetap valid,
# tapi credential fresh lebih aman untuk VM baru). Aman: hanya overwrite
# jika http_proxy memang ada di env (sudo tanpa env tidak akan merusak file).
if [ -n "${http_proxy:-}" ]; then
    {
        printf 'http_proxy=%q\n' "$http_proxy"
        printf 'https_proxy=%q\n' "${https_proxy:-$http_proxy}"
        printf 'HTTP_PROXY=%q\n' "${HTTP_PROXY:-$http_proxy}"
        printf 'HTTPS_PROXY=%q\n' "${HTTPS_PROXY:-${https_proxy:-$http_proxy}}"
    } > $RP/.proxy-env
    chmod 600 $RP/.proxy-env
    echo "proxy env refreshed from current shell"
fi

# 0. Pastikan cloudflared tersedia.
# Urutan: backup lokal ($RP/bin, persisten) -> download via proxy ->
# gagal total (instruksi manual). Tanpa fallback download, clone bersih
# di VM baru tidak bisa one-shot karena binary 40MB tidak ikut ke-commit.
if [ ! -x /usr/bin/cloudflared ]; then
    if [ -x "$RP/bin/cloudflared" ]; then
        echo "cloudflared not found, restoring from backup..."
        cp "$RP/bin/cloudflared" /usr/bin/cloudflared
        chmod +x /usr/bin/cloudflared
    else
        echo "no local cloudflared backup, downloading via proxy..."
        if [ -f "$RP/.proxy-env" ]; then set -a; . "$RP/.proxy-env"; set +a; fi
        mkdir -p "$RP/bin"
        if curl -fSL --retry 2 --max-time 300 \
                -x "${https_proxy:-$http_proxy}" \
                -o "$RP/bin/cloudflared" \
                https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
           && "$RP/bin/cloudflared" --version >/dev/null 2>&1; then
            cp "$RP/bin/cloudflared" /usr/bin/cloudflared
            chmod +x /usr/bin/cloudflared
            echo "cloudflared downloaded OK"
        else
            echo "FATAL: cloudflared tidak tersedia dan download gagal."
            echo "Download manual dari https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
            echo "lalu taruh di $RP/bin/cloudflared dan jalankan ulang script ini."
            exit 1
        fi
    fi
fi
ln -sf /usr/bin/cloudflared /usr/local/bin/cloudflared

# 1. Pastikan openssh-server terpasang (restore dari deb_cache jika container baru/wiped)
if ! command -v sshd >/dev/null 2>&1; then
    echo "sshd not found, installing from offline deb_cache..."
    if ls $RP/deb_cache/*.deb >/dev/null 2>&1; then
        dpkg -i $RP/deb_cache/*.deb
    else
        echo "deb_cache missing, attempting apt install..."
        # source proxy env dengan benar (jangan grep: quoting bisa rusak)
        if [ -f $RP/.proxy-env ]; then set -a; . $RP/.proxy-env; set +a; fi
        apt update && apt install -y openssh-server
    fi
fi

# 2. Restore konfigurasi sshd jika ada backup
if [ -f "$RP/sshd_config" ]; then
    mkdir -p /etc/ssh
    cp "$RP/sshd_config" /etc/ssh/sshd_config
fi

# 2b. Restore SSH host keys jika ada backup (hindari warning
# "host identification changed" di client setelah VM reset)
if [ -d "$RP/ssh_host_keys" ] && ls "$RP/ssh_host_keys"/ssh_host_* >/dev/null 2>&1; then
    mkdir -p /etc/ssh
    cp "$RP/ssh_host_keys"/ssh_host_* /etc/ssh/
    chmod 600 /etc/ssh/ssh_host_*_key 2>/dev/null || true
    chmod 644 /etc/ssh/ssh_host_*_key.pub 2>/dev/null || true
fi

# 3. Restore password root jika ada backup hash
if [ -f "$RP/.root_shadow" ]; then
    PASS_HASH=$(cat "$RP/.root_shadow")
    if [ -n "$PASS_HASH" ]; then
        usermod -p "$PASS_HASH" root
    fi
fi

# 4. Install & reload service systemd
cp $RP/rp-tunnel.service /etc/systemd/system/rp-tunnel.service
systemctl daemon-reload
systemctl enable --now rp-tunnel.service
systemctl enable --now ssh

# 4b. Install hatch-panel (web monitor + terminal)
cp /home/hatch/workspace/panel/hatch-panel.service /etc/systemd/system/hatch-panel.service
systemctl daemon-reload
systemctl enable --now hatch-panel.service

# 5. Restore agy (Antigravity CLI) binary symlink & auth token
if [ -f /home/hatch/workspace/bin/agy ]; then
    mkdir -p /usr/local/bin
    ln -sf /home/hatch/workspace/bin/agy /usr/local/bin/agy
fi
if [ -d /home/hatch/workspace/.gemini/antigravity-cli ]; then
    mkdir -p /root/.gemini
    cp -rn /home/hatch/workspace/.gemini/antigravity-cli /root/.gemini/ 2>/dev/null || true
fi

echo "autostart installed: rp-tunnel + ssh enabled and running"
systemctl is-enabled rp-tunnel.service ssh


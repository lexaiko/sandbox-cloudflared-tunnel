#!/bin/bash
# setup-autostart.sh — (re)install and restore the tunnel & ssh into system locations.
# System files outside ~ are ephemeral across VM replacements. Everything needed
# lives in ~/workspace/rp/ (persistent).
set -e
RP=/home/hatch/workspace/rp

chmod 755 $RP/rp-boot.sh
chmod 600 $RP/.token 2>/dev/null || true
chmod 600 $RP/.root_shadow 2>/dev/null || true

# 0. Pastikan cloudflared tersedia (restore dari backup jika VM baru)
if [ ! -x /usr/bin/cloudflared ]; then
    echo "cloudflared not found, restoring from backup..."
    cp $RP/bin/cloudflared /usr/bin/cloudflared
    chmod +x /usr/bin/cloudflared
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


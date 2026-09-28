#!/usr/bin/env bash
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

# Pastikan symlink agy selalu ada di /usr/local/bin
if ! command -v agy >/dev/null 2>&1; then
    if [[ -f /home/hatch/workspace/bin/agy ]]; then
        mkdir -p /usr/local/bin
        ln -sf /home/hatch/workspace/bin/agy /usr/local/bin/agy || true
    fi
fi

# Check if rp-tunnel and ssh are active
if systemctl is-active --quiet rp-tunnel.service && systemctl is-active --quiet ssh; then
    silent "tunnel_ok" '{"status":"active"}'
fi

# Data-plane watchdog: service "active" TIDAK CUKUP.
# Insiden 2026-09-29 00:02: cloudflared hidup tapi 0 koneksi edge (address pool
# habis di-backoff -> "no free edge addresses"), retry tidak jalan, tunnel mati
# tanpa ada service yang failed. Deteksi via koneksi TCP ESTABLISHED dari
# cloudflared ke relay 127.0.0.x:7844. Butuh 3x cek gagal beruntun (3 menit)
# sebelum restart, supaya reconnect normal yang singkat tidak ke-restart.
EDGE_CONNS=$(ss -tnp 2>/dev/null | grep -c 'ESTAB.*:7844.*cloudflared' || true)
WATCH_STATE=/home/hatch/workspace/rp/.watchdog-state
if [ "${EDGE_CONNS:-0}" -ge 1 ]; then
    echo 0 > "$WATCH_STATE"
else
    N=$(($(cat "$WATCH_STATE" 2>/dev/null || echo 0) + 1))
    echo "$N" > "$WATCH_STATE"
    log "data-plane check: 0 edge connections (${N}/3)"
    if [ "$N" -ge 3 ]; then
        log "cloudflared stuck with 0 edge connections, killing for auto-restart..."
        pkill -f '[c]loudflared tunnel' || true
        echo 0 > "$WATCH_STATE"
    fi
fi

# If inactive or missing, trigger setup-autostart
log "rp-tunnel or ssh inactive/missing, restoring..."
if [[ -f /home/hatch/workspace/rp/setup-autostart.sh ]]; then
    bash /home/hatch/workspace/rp/setup-autostart.sh >/dev/null 2>&1 || true
fi

if systemctl is-active --quiet rp-tunnel.service; then
    silent "tunnel_restored" '{"status":"restored"}'
else
    wake "tunnel_failed" '{"status":"failed_to_restore"}'
fi

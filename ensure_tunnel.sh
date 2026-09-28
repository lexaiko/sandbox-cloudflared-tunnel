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

# ---------------------------------------------------------------------------
# Data-plane watchdog — HARUS jalan tiap tick, bahkan saat service "active".
#
# Bug yang diperbaiki 2026-09-29 ~02:10: versi sebelumnya menaruh cek
# "systemctl is-active" + early-exit `silent` PALING ATAS, sehingga watchdog
# di bawahnya TIDAK PERNAH jalan saat service active — padahal insiden 00:02
# persis itu: service active, 0 koneksi edge, cloudflared stuck diam.
#
# Definisi "stuck": 0 koneksi edge ESTABLISHED dari cloudflared ke relay
# (127.0.0.x:7844) DAN cf-boot.log diam >3 menit -> kill, rp-boot.sh akan
# restart cloudflared dalam 10 detik.
# Kalau log masih bergerak (retry/backoff aktif), JANGAN dibunuh — biarkan
# cloudflared kerja. Pelajaran insiden 00:42: retry agresif bisa kena
# throttle proxy; membunuh proses yang sedang retry justru memperparah.
# ---------------------------------------------------------------------------
EDGE_CONNS=$(ss -tnp 2>/dev/null | grep -c 'ESTAB.*:7844.*cloudflared' || true)
WATCH_STATE=/home/hatch/workspace/rp/.watchdog-state
CF_LOG=/home/hatch/workspace/rp/cf-boot.log
if [ "${EDGE_CONNS:-0}" -ge 1 ]; then
    echo 0 > "$WATCH_STATE"
else
    LOG_AGE=$(( $(date +%s) - $(stat -c %Y "$CF_LOG" 2>/dev/null || echo 0) ))
    if [ "$LOG_AGE" -gt 180 ]; then
        N=$(($(cat "$WATCH_STATE" 2>/dev/null || echo 0) + 1))
        echo "$N" > "$WATCH_STATE"
        if [ "$N" -ge 2 ]; then
            log "watchdog: 0 edge conns + cf-boot.log diam ${LOG_AGE}s -> kill stuck cloudflared"
            pkill -f '[c]loudflared tunnel' || true
            echo 0 > "$WATCH_STATE"
        else
            log "watchdog: 0 edge conns + cf-boot.log diam ${LOG_AGE}s (${N}/2)"
        fi
    else
        # cloudflared masih aktif retry (log bergerak) -> jangan ganggu
        echo 0 > "$WATCH_STATE"
    fi
fi

# Check if rp-tunnel and ssh are active
if systemctl is-active --quiet rp-tunnel.service && systemctl is-active --quiet ssh; then
    silent "tunnel_ok" '{"status":"active"}'
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

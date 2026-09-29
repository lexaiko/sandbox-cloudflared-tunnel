#!/bin/bash
# rp-boot.sh — autostart chain for the reverse-proxy tunnel.
# Runs as rp-tunnel.service (Type=simple, Restart=always). systemd kills the
# whole cgroup on restart, so relay/dns/cloudflared are all cleaned up together.
# Runs inside the service's mount namespace, where systemd has bind-mounted:
#   ~/workspace/rp/hosts       -> /etc/hosts
#   ~/workspace/rp/resolv.conf -> /etc/resolv.conf
set -u
RP=/home/hatch/workspace/rp
LOG=$RP/boot.log

# egress proxy (systemd services don't inherit the shell's proxy env)
if [ -f $RP/.proxy-env ]; then
  set -a; . $RP/.proxy-env; set +a
  echo "proxy env loaded"
else
  echo "FATAL: $RP/.proxy-env missing" >> $LOG; exit 1
fi

{
echo "=== rp-boot $(date -u +%FT%TZ) ==="

# 1. TCP relay (cloudflared -> egress proxy)
python3 $RP/tcprelay.py >> $RP/relay.log 2>&1 &
echo "relay pid $!"

# 2. DNS server (SRV edge discovery + faked Cloudflare names)
python3 $RP/dns.py >> $RP/dns.log 2>&1 &
echo "dns pid $!"

# 3. wait until local DNS answers the exact SRV query cloudflared needs
for i in $(seq 1 30); do
  if python3 -c "
import socket,struct
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('127.0.0.1',53)); s.settimeout(2)
h=struct.pack('>HHHHHH',7,0x0100,1,0,0,0)
for p in '_v2-origintunneld _tcp argotunnel com'.split(): h+=bytes([len(p)])+p.encode()
h+=b'\x00'+struct.pack('>HH',33,1)
s.send(h); d=s.recv(2048)
assert struct.unpack('>H',d[6:8])[0] >= 1
print('srv-ok')
" 2>/dev/null | grep -q srv-ok; then
    echo "dns SRV ready after ${i}s"
    break
  fi
  sleep 1
done

# 4. sshd for the tunnel's localhost:22 origin
# Prefer systemd's ssh.service (socket-activated) when it is enabled/active.
# Starting a standalone sshd here would steal :22 and make ssh.socket fail
# every minute (seen 2026-09-29: ssh.service stuck "dependency failed" for 7h
# because standalone sshd from this step owned the port).
mkdir -p /run/sshd
if systemctl is-active --quiet ssh.service 2>/dev/null; then
  echo "sshd provided by systemd ssh.service, skipping standalone start"
elif ! pgrep -x sshd >/dev/null; then
  /usr/sbin/sshd && echo "sshd started (standalone)" || echo "sshd start FAILED"
else
  echo "sshd already running"
fi

echo "=== launching cloudflared (foreground, auto-retry) ==="
} >> $LOG 2>&1

# 5. cloudflared data plane in foreground; retry forever on failure
while true; do
  /usr/local/bin/cloudflared tunnel --protocol http2 run \
    --token "$(cat $RP/.token)" >> $RP/cf-boot.log 2>&1
  echo "$(date -u +%FT%TZ) cloudflared exited ($?), retry in 10s" >> $LOG
  sleep 10
done

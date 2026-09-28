#!/usr/bin/env python3
"""Pure-TCP CONNECT relay. Listens on 0.0.0.0:443 and :7844; maps the
destination 127.0.0.x (set via /etc/hosts bind-mount) back to a hostname
and opens an HTTP CONNECT tunnel through the sandbox egress proxy.
Transparent byte splice afterwards (works for TLS + HTTP/2)."""
import socket, threading, select, os, sys, base64
from urllib.parse import urlparse

# 127.0.0.x -> real hostname (must match hosts file entries)
MAP = {
    "127.0.0.2": "api.trycloudflare.com",
    "127.0.0.3": "region1.v2.argotunnel.com",
    "127.0.0.4": "region2.v2.argotunnel.com",
    "127.0.0.5": "region3.v2.argotunnel.com",
    "127.0.0.6": "region4.v2.argotunnel.com",
    "127.0.0.7": "region5.v2.argotunnel.com",
    "127.0.0.8": "region6.v2.argotunnel.com",
    "127.0.0.9": "region7.v2.argotunnel.com",
    "127.0.0.10": "region8.v2.argotunnel.com",
}
LISTEN_PORTS = (443, 7844)

def get_proxy():
    u = (os.environ.get("SHIM_PROXY") or os.environ.get("https_proxy")
         or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or "")
    p = urlparse(u)
    auth = None
    if p.username:
        auth = base64.b64encode(
            ("%s:%s" % (p.username, p.password or "")).encode()).decode()
    return p.hostname, p.port or 3128, auth

PX_HOST, PX_PORT, PX_AUTH = get_proxy()
if not PX_HOST:
    sys.stderr.write("no proxy in env\n"); sys.exit(1)
PX_IP = socket.getaddrinfo(PX_HOST, PX_PORT, socket.AF_INET,
                           socket.SOCK_STREAM)[0][4][0]
sys.stderr.write("relay up on %s\n" % (LISTEN_PORTS,))

def connect_via_proxy(host, port):
    s = socket.create_connection((PX_IP, PX_PORT), timeout=15)
    req = "CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n" % (host, port, host, port)
    if PX_AUTH:
        req += "Proxy-Authorization: Basic %s\r\n" % PX_AUTH
    req += "\r\n"
    s.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        ch = s.recv(4096)
        if not ch:
            raise RuntimeError("proxy closed")
        resp += ch
        if len(resp) > 8192:
            raise RuntimeError("proxy resp too big")
    if b" 200" not in resp.split(b"\r\n", 1)[0]:
        raise RuntimeError("proxy refused")
    return s

def splice(a, b):
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], 180)
            if not r:
                break
            for s in r:
                d = s.recv(65536)
                if not d:
                    return
                (b if s is a else a).sendall(d)
    except Exception:
        pass
    finally:
        for s in (a, b):
            try: s.close()
            except Exception: pass

def handle(client):
    try:
        dst_ip, dst_port = client.getsockname()[0], client.getsockname()[1]
        host = MAP.get(dst_ip)
        if not host:
            client.close(); return
        sys.stderr.write("relay %s:%d -> %s:%d\n" % (dst_ip, dst_port, host, dst_port))
        up = connect_via_proxy(host, dst_port)
        splice(client, up)
    except Exception as e:
        sys.stderr.write("relay error: %s\n" % e)
        try: client.close()
        except Exception: pass

def serve(port):
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("0.0.0.0", port))
    ls.listen(100)
    while True:
        c, _ = ls.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()

for p in LISTEN_PORTS:
    threading.Thread(target=serve, args=(p,), daemon=True).start()
threading.Event().wait()

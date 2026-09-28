#!/usr/bin/env python3
"""
ws-edge-wrap.py — pengganti tcprelay.py untuk jaringan yang proxy-nya
membunuh TLS langsung ke *.v2.argotunnel.com:7844.

Cara kerja: listen di 127.0.0.x:7844 (IP palsu dari /etc/hosts, lihat MAP —
SAMA persis seperti tcprelay.py). Setiap koneksi TCP yang masuk dibungkus
menjadi WebSocket (WSS) ke Cloudflare Worker (edge-wrap-worker.js), yang
membuka TCP mentah ke edge yang sebenarnya.

Proxy hanya melihat: WSS (HTTPS biasa) ke <worker>.workers.dev:443.
Tidak ada SNI argotunnel.com, tidak ada koneksi ke :7844 yang terlihat.

cloudflared tetap jalan TANPA MODIFIKASI — cukup ganti tcprelay.py dengan
script ini untuk port 7844. dns.py + /etc/hosts + /etc/resolv.conf tetap sama.

Env yang dibutuhkan:
  WRAP_WORKER   hostname worker, mis. edge-wrap.namakamu.workers.dev
  WRAP_TOKEN    token rahasia (sama dengan WRAP_TOKEN di Worker)
  https_proxy / HTTPS_PROXY  (atau http_proxy) — proxy egress, seperti tcprelay.py
Hanya stdlib Python. Tidak ada dependensi.
"""
import socket
import threading
import ssl
import os
import base64
import hashlib
import struct
import sys
from urllib.parse import urlparse

# 127.0.0.x -> hostname edge asli (HARUS sama dengan /etc/hosts palsu)
MAP = {
    "127.0.0.3": "region1.v2.argotunnel.com",
    "127.0.0.4": "region2.v2.argotunnel.com",
    "127.0.0.5": "region3.v2.argotunnel.com",
    "127.0.0.6": "region4.v2.argotunnel.com",
    "127.0.0.7": "region5.v2.argotunnel.com",
    "127.0.0.8": "region6.v2.argotunnel.com",
    "127.0.0.9": "region7.v2.argotunnel.com",
    "127.0.0.10": "region8.v2.argotunnel.com",
}
PORT = 7844

WORKER = os.environ.get("WRAP_WORKER", "")
TOKEN = os.environ.get("WRAP_TOKEN", "")


def get_proxy():
    u = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
         or os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or "")
    p = urlparse(u)
    auth = None
    if p.username:
        auth = base64.b64encode(
            ("%s:%s" % (p.username, p.password or "")).encode()).decode()
    return p.hostname, p.port or 3128, auth


PX_HOST, PX_PORT, PX_AUTH = get_proxy()
if not (PX_HOST and WORKER and TOKEN):
    sys.stderr.write("ws-edge-wrap: butuh proxy env + WRAP_WORKER + WRAP_TOKEN\n")
    sys.exit(1)
PX_IP = socket.getaddrinfo(PX_HOST, PX_PORT, socket.AF_INET,
                           socket.SOCK_STREAM)[0][4][0]


class WSConn:
    """Klien WebSocket minimal (RFC 6455) di atas socket yang sudah TLS."""

    def __init__(self, sock):
        self.s = sock
        self.buf = bytearray()

    def _fill(self, n):
        while len(self.buf) < n:
            ch = self.s.recv(65536)
            if not ch:
                raise ConnectionError("ws closed")
            self.buf += ch

    def send(self, data, opcode=0x2):
        mask = os.urandom(4)
        hdr = bytes([0x80 | opcode])  # FIN + opcode
        ln = len(data)
        if ln < 126:
            hdr += bytes([0x80 | ln])
        elif ln < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", ln)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", ln)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
        self.s.sendall(hdr + mask + masked)

    def recv_msg(self):
        """Payload message lengkap berikutnya, atau None saat close."""
        frag = bytearray()
        while True:
            self._fill(2)
            b1, b2 = self.buf[0], self.buf[1]
            fin, op = b1 & 0x80, b1 & 0x0F
            ln = b2 & 0x7F
            i = 2
            if ln == 126:
                self._fill(4)
                ln = struct.unpack(">H", self.buf[2:4])[0]
                i = 4
            elif ln == 127:
                self._fill(10)
                ln = struct.unpack(">Q", self.buf[2:10])[0]
                i = 10
            if b2 & 0x80:
                self._fill(i + 4)
                mask = self.buf[i:i + 4]
                i += 4
            else:
                mask = None
            self._fill(i + ln)
            payload = bytes(self.buf[i:i + ln])
            del self.buf[:i + ln]
            if mask:
                payload = bytes(b ^ mask[j & 3] for j, b in enumerate(payload))
            if op == 0x8:                       # close
                return None
            if op == 0x9:                       # ping -> pong
                self.send(b"", opcode=0xA)
                continue
            if op in (0x1, 0x2):                # text / binary: frame baru
                frag = bytearray(payload)
            elif op == 0x0:                     # continuation
                frag += payload
            else:
                continue
            if fin:
                return bytes(frag)


def ws_handshake(target):
    """Buka WSS ke Worker untuk target edge. Return WSConn yang sudah jadi."""
    s = socket.create_connection((PX_IP, PX_PORT), timeout=20)
    req = "CONNECT %s:443 HTTP/1.1\r\nHost: %s:443\r\n" % (WORKER, WORKER)
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
        raise RuntimeError("proxy refused CONNECT to worker")

    ctx = ssl.create_default_context()
    t = ctx.wrap_socket(s, server_hostname=WORKER)

    key = base64.b64encode(os.urandom(16)).decode()
    path = "/edge/%s/%d?token=%s" % (target, PORT, TOKEN)
    hs = ("GET %s HTTP/1.1\r\n"
          "Host: %s\r\n"
          "Upgrade: websocket\r\n"
          "Connection: Upgrade\r\n"
          "Sec-WebSocket-Key: %s\r\n"
          "Sec-WebSocket-Version: 13\r\n"
          "\r\n") % (path, WORKER, key)
    t.sendall(hs.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        ch = t.recv(4096)
        if not ch:
            raise RuntimeError("worker closed during handshake")
        resp += ch
        if len(resp) > 16384:
            raise RuntimeError("handshake too big")
    status = resp.split(b"\r\n", 1)[0]
    if b" 101" not in status:
        raise RuntimeError("ws upgrade failed: %s" % status.decode(errors="replace"))
    return WSConn(t)


def handle(client):
    try:
        dst_ip = client.getsockname()[0]
        target = MAP.get(dst_ip)
        if not target:
            sys.stderr.write("wrap: no mapping for %s\n" % dst_ip)
            client.close()
            return
        ws = ws_handshake(target)
        sys.stderr.write("wrap %s:%d -> %s via worker %s\n" % (dst_ip, PORT, target, WORKER))

        def c2w():
            try:
                while True:
                    d = client.recv(65536)
                    if not d:
                        break
                    ws.send(d)
            except Exception:
                pass
            finally:
                try:
                    ws.send(b"", opcode=0x8)
                except Exception:
                    pass

        th = threading.Thread(target=c2w, daemon=True)
        th.start()
        try:
            while True:
                m = ws.recv_msg()
                if m is None:
                    break
                client.sendall(m)
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
    except Exception as e:
        sys.stderr.write("wrap error: %r\n" % e)
        try:
            client.close()
        except Exception:
            pass


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(128)
    sys.stderr.write("ws-edge-wrap up on :%d -> worker %s\n" % (PORT, WORKER))
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()

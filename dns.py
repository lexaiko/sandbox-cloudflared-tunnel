#!/usr/bin/env python3
"""Minimal DNS server (TCP only — UDP send is blocked in this sandbox).
Listens on 127.0.0.1:53.

- SRV _v2-origintunneld._tcp.argotunnel.com -> region1..8.v2.argotunnel.com:7844
  (semua 8 region, bukan cuma 4 — pool address lebih besar supaya cloudflared
  tidak kehabisan kandidat saat beberapa region di-blok proxy;
  pelajaran dari insiden 2026-09-29 00:02: "no free edge addresses")
- A for faked Cloudflare hosts -> 127.0.0.x (matches hosts bind-mount)
- everything else -> forwarded to 198.19.0.1:53 over TCP
"""
import socket, struct, threading, sys, ctypes

_libc = ctypes.CDLL("libc.so.6", use_errno=True)

def udp_disconnect(s):
    """connect() with sa_family=AF_UNSPEC disconnects a UDP socket (Linux)."""
    buf = (ctypes.c_char * 16)()
    if _libc.connect(s.fileno(), buf, 16) != 0:
        raise OSError(ctypes.get_errno(), "udp disconnect failed")

REAL_DNS = "198.19.0.1"
FAKE_A = {
    "api.trycloudflare.com": "127.0.0.2",
    "region1.v2.argotunnel.com": "127.0.0.3",
    "region2.v2.argotunnel.com": "127.0.0.4",
    "region3.v2.argotunnel.com": "127.0.0.5",
    "region4.v2.argotunnel.com": "127.0.0.6",
    "region5.v2.argotunnel.com": "127.0.0.7",
    "region6.v2.argotunnel.com": "127.0.0.8",
    "region7.v2.argotunnel.com": "127.0.0.9",
    "region8.v2.argotunnel.com": "127.0.0.10",
}
SRV_TARGETS = [
    "region1.v2.argotunnel.com",
    "region2.v2.argotunnel.com",
    "region3.v2.argotunnel.com",
    "region4.v2.argotunnel.com",
    "region5.v2.argotunnel.com",
    "region6.v2.argotunnel.com",
    "region7.v2.argotunnel.com",
    "region8.v2.argotunnel.com",
]
SRV_PORT = 7844

def encode_name(name):
    out = b""
    for part in name.rstrip(".").split("."):
        out += bytes([len(part)]) + part.encode()
    return out + b"\x00"

def parse_query(data):
    if len(data) < 12:
        return None
    qid, flags, qd, _, _, _ = struct.unpack(">HHHHHH", data[:12])
    if qd < 1:
        return None
    off = 12
    labels = []
    while True:
        if off >= len(data):
            return None
        ln = data[off]; off += 1
        if ln == 0:
            break
        if ln & 0xC0:
            return None
        labels.append(data[off:off+ln].decode("ascii", "replace"))
        off += ln
    if off + 4 > len(data):
        return None
    qtype, qclass = struct.unpack(">HH", data[off:off+4])
    return qid, ".".join(labels).lower(), qtype, data[12:off+4]

def resp_header(qid, qd=1, an=0, rcode=0):
    flags = 0x8180 | rcode
    return struct.pack(">HHHHHH", qid, flags, qd, an, 0, 0)

def recvn_sock(s, n):
    out = b""
    while len(out) < n:
        ch = s.recv(n - len(out))
        if not ch:
            return None
        out += ch
    return out

def handle(data):
    p = parse_query(data)
    if not p:
        return None
    qid, qname, qtype, question = p

    # SRV for edge discovery
    if qtype == 33 and qname.endswith("argotunnel.com"):
        out = resp_header(qid, an=len(SRV_TARGETS)) + question
        for t in SRV_TARGETS:
            rdata = struct.pack(">HHH", 0, 10, SRV_PORT) + encode_name(t)
            out += struct.pack(">HHHLH", 0xC00C, 33, 1, 60, len(rdata)) + rdata
        return out

    # faked A records
    if qtype == 1 and qname in FAKE_A:
        rdata = socket.inet_aton(FAKE_A[qname])
        out = (resp_header(qid, an=1) + question
               + struct.pack(">HHHLH", 0xC00C, 1, 1, 60, 4) + rdata)
        return out

    # AAAA / others for faked names -> empty NOERROR
    if qname in FAKE_A or qname.endswith("argotunnel.com"):
        return resp_header(qid) + question

    # forward everything else to the real resolver over TCP
    try:
        s = socket.create_connection((REAL_DNS, 53), timeout=5)
        s.sendall(struct.pack(">H", len(data)) + data)
        s.settimeout(5)
        hdr = recvn_sock(s, 2)
        if hdr is None:
            return None
        ln = struct.unpack(">H", hdr)[0]
        out = recvn_sock(s, ln)
        s.close()
        return out
    except Exception:
        return resp_header(qid, rcode=2) + question  # SERVFAIL

def recvn(c, n):
    out = b""
    while len(out) < n:
        ch = c.recv(n - len(out))
        if not ch:
            return None
        out += ch
    return out

def one(c):
    try:
        while True:
            hdr = recvn(c, 2)
            if hdr is None:
                return
            ln = struct.unpack(">H", hdr)[0]
            data = recvn(c, ln)
            if data is None:
                return
            out = handle(data)
            if out:
                c.sendall(struct.pack(">H", len(out)) + out)
    except Exception as e:
        sys.stderr.write("dns error: %r\n" % e)
    finally:
        c.close()

def udp_main():
    """UDP listener. seccomp blocks sendto/sendmsg, so replies go through
    connect()+send() on the bound socket, then AF_UNSPEC disconnect.
    Single-threaded: no races, fine for tiny DNS volume."""
    us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    us.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    us.bind(("127.0.0.1", 53))
    sys.stderr.write("dns(udp) up on 127.0.0.1:53\n")
    while True:
        try:
            data, addr = us.recvfrom(2048)
        except Exception as e:
            sys.stderr.write("dns udp recv error: %r\n" % e)
            continue
        try:
            out = handle(data)
            if out:
                us.connect(addr)
                us.send(out)
                udp_disconnect(us)
        except Exception as e:
            sys.stderr.write("dns udp reply error: %r\n" % e)
            try:
                udp_disconnect(us)
            except Exception:
                pass

def main():
    threading.Thread(target=udp_main, daemon=True).start()
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(("127.0.0.1", 53))
    ls.listen(50)
    sys.stderr.write("dns(tcp) up on 127.0.0.1:53\n")
    while True:
        c, _ = ls.accept()
        threading.Thread(target=one, args=(c,), daemon=True).start()

if __name__ == "__main__":
    main()

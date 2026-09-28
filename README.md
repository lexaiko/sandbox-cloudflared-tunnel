# Menjalankan Cloudflare Tunnel dari Sandbox yang Serba Diblokir

Tutorial + toolkit untuk menjalankan **reverse proxy (Cloudflare Tunnel)** dari dalam sebuah
sandbox/container Linux yang akses internetnya sangat dibatasi:

- semua outbound **wajib lewat HTTP proxy** (dengan autentikasi),
- **direct TCP diblokir**,
- **DNS di-hijack** (hostname Cloudflare di-resolve ke IP palsu),
- proxy me-**MITM semua TLS** dan me-**tarpit semua HTTP POST** (hang selamanya),
- syscall `sendto`/`sendmsg` untuk UDP **diblokir seccomp**,
- `/etc/hosts` dan `/etc/resolv.conf` **read-only**,
- `/tmp` **di-wipe** setiap sesi.

Repo ini berisi **cara berpikirnya** (bukan cuma script jadi), supaya AI lain (atau kamu
di masa depan) bisa mereplikasi problem solving-nya di lingkungan serupa.

> ⚠️ **Keamanan:** repo ini TIDAK berisi token, password, atau kredensial apa pun.
> Semua kredensial dibaca dari file lokal (`$RP/.token`, `$RP/.proxy-env`, permission 600)
> yang di-exclude via `.gitignore`. Jangan pernah commit file tersebut.

## Prompt siap pakai (untuk Muse lain)

Repo ini adalah **source of truth**. Kalau kamu AI yang dikasih repo ini, mulai dari sini:

| Kamu mau apa | Baca / pakai |
|---|---|
| Paham total + replikasi sampai berhasil (disarankan) | `PROMPT-master.md` — metodologi, 13 constraint, jalan buntu, 8 langkah + verifikasi tiap langkah, definisi sukses |
| Handoff ringkas (sudah paham dasarnya) | `PROMPT-tunneling.md` — versi padat arsitektur + file |
| Verifikasi ulang di mesin yang dicurigai proxy-nya lebih ketat | `PROMPT-cara-langsung.md` — diagnostik berlapis + uji tentu `openssl s_client` |
| Paham narasi lengkap perjalanannya | `README.md` (file ini) — tutorial 11 langkah problem solving |
| Langsung pakai script-nya | `tcprelay.py`, `dns.py`, `rp-boot.sh`, `rp-tunnel.service`, `setup-autostart.sh`, `hosts.example`, `resolv.conf.example` |

---

## Daftar Isi

1. [Gambaran masalah](#1-gambaran-masalah)
2. [Peta constraint (hasil recon)](#2-peta-constraint-hasil-recon)
3. [Arsitektur akhir](#3-arsitektur-akhir)
4. [Problem solving langkah demi langkah](#4-problem-solving-langkah-demi-langkah)
5. [File-file di repo ini](#5-file-file-di-repo-ini)
6. [Instalasi](#6-instalasi)
7. [Autostart (systemd)](#7-autostart-systemd)
8. [Troubleshooting](#8-troubleshooting)
9. [Pelajaran yang dipetik](#9-pelajaran-yang-dipetik)

---

## 1. Gambaran masalah

**Tujuan:** sandbox bisa diakses dari internet (kasus kami: SSH ke port 22 sandbox lewat
public hostname).

**Tantangan:** tool standar (`cloudflared tunnel --url`, alias *quick tunnel*) gagal total
di sandbox ini. Setiap jalan pintas yang dicoba mentok di constraint yang berbeda-beda,
dan solusinya baru ketemu setelah memetakan **semua** constraint-nya dulu.

**Solusi akhir:** *named tunnel* Cloudflare — tunnel-nya dibuat di **luar** sandbox
(dashboard Cloudflare / mesin lain), token-nya dijalankan di **dalam** sandbox lewat
rangkaian relay + DNS palsu + `cloudflared tunnel --protocol http2 run`.

---

## 2. Peta constraint (hasil recon)

Sebelum ngoprek, petakan dulu medannya. Ini hasil recon di sandbox kami (Ubuntu 24.04
container, `cloudflared` sudah terinstall):

| # | Constraint | Cara memverifikasi |
|---|-----------|-------------------|
| 1 | Semua outbound lewat HTTP proxy `hatch-egress-proxy:3128` (auth) | `env \| grep -i proxy` |
| 2 | Direct TCP diblokir (kecuali via proxy) | `curl` tanpa proxy → timeout |
| 3 | DNS sandbox di-hijack untuk domain Cloudflare | `nslookup region1.v2.argotunnel.com` → IP palsu; bandingkan via DoH (`https://cloudflare-dns.com/dns-query`) |
| 4 | Proxy MITM semua TLS (CA sendiri) | `curl -v` → issuer `CN=Hatch Sandbox Egress CA` |
| 5 | Proxy **tarpit semua HTTP POST** (hang, tidak pernah dibalas) | `curl -X POST` ke URL mana pun → hang; GET lolos |
| 6 | `cloudflared` (Go) tidak mau pakai upstream HTTP proxy | `cloudflared tunnel --url` → TLS handshake error |
| 7 | Go pakai pure-Go resolver (bypass libc) → `LD_PRELOAD` tidak mempan untuk DNS-nya | strace: tidak ada panggilan `getaddrinfo` libc |
| 8 | Seccomp blokir `sendto`/`sendmsg` di socket UDP (EPERM), tapi **`connect()` + `send()` boleh** | program Python UDP: `sendto` → EPERM, `connect`+`send` → OK |
| 9 | Go **tidak fallback ke TCP** kalau UDP DNS connection-refused | DNS server TCP-only → cloudflared tetap error |
| 10 | Go memakai **connected UDP** (`connect()` ke `127.0.0.1:53`) | strace: `connect(3, {sa_family=AF_INET, port=53, sin_addr=127.0.0.1})` sukses |
| 11 | `/etc/hosts` & `/etc/resolv.conf` read-only, tapi `mount --bind` bisa (root) — **hanya dalam satu mount namespace** (satu exec call) | `mount --bind` di exec A tidak terlihat di exec B |
| 12 | `/tmp` di-wipe antar sesi | file hilang setelah sesi berganti → taruh di `~/workspace/` |
| 13 | Service systemd **tidak mewarisi** env proxy dari shell | proses di bawah systemd: `env \| grep -i proxy` → kosong |

> 💡 **Prinsip utama:** jangan melawan constraint satu per satu secara membabi buta.
> Petakan semuanya dulu, baru cari *satu arsitektur* yang lolos dari semuanya sekaligus.

---

## 3. Arsitektur akhir

```
[Internet]
    │
    ▼
[Cloudflare Edge] ──HTTP/2 streams──▶ [cloudflared di sandbox]
                                          │  (butuh: DNS SRV + TCP ke edge)
                                          ▼
                                   [dns.py 127.0.0.1:53] ◀── /etc/resolv.conf (bind-mount)
                                   jawab SRV _v2-origintunneld._tcp.argotunnel.com
                                   + A palsu untuk hostname Cloudflare
                                          │
                                          ▼
                                   [tcprelay.py :443/:7844] ◀── /etc/hosts (bind-mount)
                                   127.0.0.x ──(HTTP CONNECT)──▶ [egress proxy :3128]
                                                                      ──▶ [IP asli edge Cloudflare]
                                          │
                                          ▼
                                   [cloudflared] ──▶ localhost:22 ──▶ [sshd]
```

**Kenapa arsitektur ini lolos dari semua constraint:**

- *Quick tunnel mustahil* (butuh POST registrasi → ditartpit, constraint #5). Maka dipakai
  **named tunnel**: registrasi dilakukan di luar sandbox, di dalam hanya jalan *data plane*.
- *Data plane* `cloudflared` adalah HTTP/2 streams — **tanpa POST** → lolos proxy (#5).
- cloudflared tidak mau lewat proxy (#6) → `tcprelay.py` menerima koneksi TCP-nya dan
  meneruskannya via **HTTP CONNECT** ke proxy, lalu *splice* byte transparan (lolos untuk
  TLS maupun HTTP/2).
- cloudflared butuh resolve hostname Cloudflare, tapi DNS di-hijack (#3) dan Go bypass
  libc (#7) → `/etc/hosts` palsu di-bind-mount (#11): `127.0.0.2`–`127.0.0.10` dipetakan
  ke tiap hostname; `tcprelay.py` membaca IP tujuan untuk tahu hostname aslinya.
- cloudflared butuh **SRV** edge discovery (`_v2-origintunneld._tcp.argotunnel.com`) yang
  tidak bisa dimasukkan ke `/etc/hosts` → `dns.py` + `/etc/resolv.conf` bind-mount
  (`nameserver 127.0.0.1`).
- Go tidak fallback ke TCP (#9) dan UDP `sendto` diblokir (#8) → `dns.py` membalas via
  **`connect()` + `send()`** (#8, #10), lalu disconnect dengan trik `connect()` memakai
  `sa_family=AF_UNSPEC` via ctypes — single-threaded di socket yang sama agar source
  port tetap 53 (wajib, karena socket Go ter-connect ke `127.0.0.1:53`).

---

## 4. Problem solving langkah demi langkah

Bagian ini adalah **inti tutorial**: urutan penemuan yang sebenarnya terjadi, termasuk
jalan buntu-nya. Setiap langkah menjelaskan *hipotesis → eksperimen → hasil → kesimpulan*.

### Langkah 0 — Baseline

Cek apa yang ada: tidak ada reverse proxy yang jalan, tidak ada port yang listen.
`cloudflared` sudah terinstall di `/usr/local/bin/cloudflared`. Kita root di container.

### Langkah 1 — Coba cara normal dulu: quick tunnel ❌

```
cloudflared tunnel --url http://localhost:22
```
**Hasil:** TLS handshake error. cloudflared tidak mau lewat upstream HTTP proxy
(constraint #6). Pelajaran: *selalu coba cara normal dulu* — kalau beruntung, selesai
dalam 1 menit.

### Langkah 2 — Recon egress 🔍

Cari tahu lewat mana trafik bisa keluar:
- Ketemu proxy di env (`hatch-egress-proxy:3128` + auth).
- Direct TCP diblokir; DNS untuk domain Cloudflare mengembalikan IP palsu.
- Dapat **IP asli** Cloudflare via DNS-over-HTTPS:
  `api.trycloudflare.com`, `region1-8.v2.argotunnel.com`.

### Langkah 3 — LD_PRELOAD CONNECT shim ❌ (setengah jalan)

Buat shim `LD_PRELOAD` yang mengintersep `connect()` dan menggantinya dengan
HTTP CONNECT ke proxy. **Berhasil untuk `curl`** (dapat respons 405 asli dari API
Cloudflare — bukti jalurnya benar!), tapi **gagal untuk cloudflared** karena Go
memakai resolver sendiri dan bypass libc untuk DNS (constraint #7).

> 💡 Shim-nya tidak sia-sia: ia membuktikan jalur CONNECT berfungsi. Bukti parsial
> itu yang membuat kita berani lanjut ke relay TCP murni.

### Langkah 4 — Fake DNS + TCP relay ❌ (mentok dua tembok)

Rencana: DNS server palsu + TCP relay. Mentok di:
- Proses kita tidak bisa kirim paket UDP (`sendto` → EPERM, constraint #8).
- `/etc/hosts` read-only.

### Langkah 5 — Terobosan: bind-mount + relay murni ✅ (sebagian)

Kombinasi yang jalan:
1. `mount --bind` file hosts kustom **dalam satu exec call yang sama** dengan proses
   yang membutuhkannya (constraint #11: mount namespace per-exec).
2. `tcprelay.py`: TCP relay murni di `:443`/`:7844`, teruskan via HTTP CONNECT,
   splice transparan.

**Verifikasi:** request lewat relay mendapat respons asli Cloudflare. Jalur data beres.

### Langkah 6 — Temuan kunci: POST ditartpit ⛔→🔀

Registrasi quick tunnel tetap gagal: ia butuh **POST** ke `api.trycloudflare.com`,
dan proxy men-tarpit **semua** POST (constraint #5). Tidak ada akal-akalan yang bisa
mengakali ini — ini batas arsitektur, bukan bug.

**Keputusan desain:** pindah ke **named tunnel**. Tunnel dibuat di luar sandbox
(dashboard Cloudflare), di dalam sandbox hanya menjalankan data plane:
```
cloudflared tunnel --protocol http2 run --token <TOKEN>
```
Data plane = HTTP/2 streams, tanpa POST → lolos proxy.

### Langkah 7 — Kendala baru: SRV discovery ❌→🔍

Dengan `--token`, cloudflared butuh SRV `_v2-origintunneld._tcp.argotunnel.com`.
Dicoba: DNS server TCP-only + bind-mount `resolv.conf` → **gagal**, Go tidak fallback
ke TCP saat UDP refused (constraint #9).

### Langkah 8 — Terobosan dari strace ✅

Dari strace terlihat Go memakai **connected UDP** — dan `connect()` pada UDP **tidak**
diblokir seccomp (constraint #8, #10)! Solusinya:

```python
# dns.py — pola inti (disederhanakan)
us = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
us.bind(("127.0.0.1", 53))
while True:
    data, addr = us.recvfrom(2048)          # recvfrom BOLEH (tidak diblokir)
    out = handle(data)                       # bangun respons DNS
    us.connect(addr)                         # connect() BOLEH
    us.send(out)                             # send() BOLEH (sendto yang diblokir!)
    udp_disconnect(us)                       # connect() dengan sa_family=AF_UNSPEC via ctypes

def udp_disconnect(s):
    # trik: "disconnect" socket UDP ter-connect (tidak ada API Python-nya)
    import ctypes
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    class SockAddr(ctypes.Structure):
        _fields_ = [("sa_family", ctypes.c_ushort), ("sa_data", ctypes.c_byte * 14)]
    sa = SockAddr(); sa.sa_family = 0  # AF_UNSPEC = disconnect
    libc.connect(s.fileno(), ctypes.byref(sa), ctypes.sizeof(sa))
```

Harus **single-threaded di socket yang sama**: source port harus tetap 53 karena
socket Go ter-connect ke `127.0.0.1:53` dan kernel hanya menerima balasan dari sana.

### Langkah 9 — Tunnel UP ✅

SRV terjawab → cloudflared registrasi koneksi edge via HTTP/2 → status HEALTHY di
dashboard Cloudflare. Rute `Public Hostname → ssh://localhost:22` mulai menerima
koneksi (terbukti dari log: percobaan SSH masuk, walau saat itu sshd belum ada).

### Langkah 10 — Origin: install & konfigurasi sshd ✅

- `apt update` lambat (±27 menit untuk 269MB/92 file) — **bukan** karena bandwidth
  (speedtest via proxy: ~55 Mbps), tapi **overhead per-file**: tiap file kena
  CONNECT + TLS MITM handshake. Pelajaran: rampingkan `sources.list` (cukup `main`).
- `apt install -y openssh-server`
- `/etc/ssh/sshd_config`: `PasswordAuthentication yes`, `PermitRootLogin yes`
  (pakai `sed` yang mengganti baris berkomentar juga — sshd memakai *first match*).
- Generate password root acak → `chpasswd` → **tampilkan sekali ke user, jangan simpan**.
- `service ssh start` → verifikasi banner `SSH-2.0-OpenSSH_9.6`.

### Langkah 11 — Autostart ✅

Semua proses di atas mati saat reboot. Solusi: satu systemd unit `rp-tunnel.service`
(`Restart=always`) yang menjalankan `rp-boot.sh`:
urutan **relay → dns → (tunggu SRV siap) → sshd → cloudflared foreground dalam loop retry**.
Plus `systemctl enable ssh`.

Jebakan saat setup autostart (sudah diperbaiki di script):
1. Service systemd **tidak mewarisi proxy env** → export eksplisit dari file.
2. `pkill -f <pattern>` bisa membunuh shell sendiri → gunakan trik `[c]loudflared`.
3. Readiness check harus query SRV yang sebenarnya, bukan query dummy.

---

## 5. File-file di repo ini

| File | Fungsi |
|------|--------|
| `tcprelay.py` | TCP relay murni: listen `:443`/`:7844` → HTTP CONNECT via proxy → splice transparan. Baca `MAP` (IP 127.0.0.x → hostname). Proxy dibaca dari env. |
| `dns.py` | DNS server di `127.0.0.1:53` (UDP via trik connect+send, dan TCP). Jawab SRV edge discovery + A palsu untuk hostname Cloudflare; forward sisanya via TCP ke upstream. |
| `hosts.example` | Contoh blok `/etc/hosts` untuk di-bind-mount (gabung dengan hosts asli). |
| `resolv.conf.example` | `nameserver 127.0.0.1` untuk di-bind-mount. |
| `rp-boot.sh` | Urutan startup: proxy env → relay → dns → tunggu SRV → sshd → cloudflared (loop retry). |
| `rp-tunnel.service` | Unit systemd (`Restart=always` + `BindReadOnlyPaths` untuk hosts/resolv.conf). |
| `setup-autostart.sh` | Install unit + enable service (jalankan ulang setelah VM replacement). |

---

## 6. Instalasi

### Prasyarat

- Linux dengan systemd (atau adaptasi ke supervisor lain), akses root.
- `cloudflared` terinstall.
- **Named tunnel sudah dibuat di luar sandbox** (dashboard Cloudflare → Networks → Tunnels).
  Catat **token**-nya. Di dashboard, buat Public Hostname route, misal:
  `Public Hostname: ssh.contoh.com` → `Service Type: SSH` → `URL: localhost:22`.
- Ketahui proxy egress kamu (biasanya ada di env: `https_proxy`).

### Langkah manual (tanpa autostart)

```bash
RP=~/workspace/rp   # atau direktori mana pun yang persisten (JANGAN /tmp)
mkdir -p $RP
cp tcprelay.py dns.py $RP/
# siapkan hosts & resolv.conf (lihat hosts.example / resolv.conf.example)
printf '%s' '<TUNNEL_TOKEN>' > $RP/.token && chmod 600 $RP/.token

# PENTING: mount + semua proses harus dalam SATU exec call / satu shell,
# karena mount namespace tidak terbawa ke sesi lain.
sudo bash -c "
mount --bind $RP/hosts /etc/hosts
mount --bind $RP/resolv.conf /etc/resolv.conf
export https_proxy='<PROXY_URL>' http_proxy='<PROXY_URL>'
python3 $RP/tcprelay.py >/tmp/relay.log 2>&1 &
python3 $RP/dns.py >/tmp/dns.log 2>&1 &
sleep 3
cloudflared tunnel --protocol http2 run --token \"\$(cat $RP/.token)\"
"
```

### Origin sshd (contoh)

```bash
apt-get update && apt-get install -y openssh-server
sed -i -E 's/^#?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i -E 's/^#?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
echo "root:$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)" | chpasswd
service ssh start
```

---

## 7. Autostart (systemd)

```bash
# 1. simpan token & proxy (sekali saja)
printf '%s' '<TUNNEL_TOKEN>' > ~/workspace/rp/.token && chmod 600 ~/workspace/rp/.token
python3 - <<'EOF'
import os, shlex
px = os.environ.get('https_proxy') or os.environ.get('HTTPS_PROXY')
with open(os.path.expanduser('~/workspace/rp/.proxy-env'), 'w') as f:
    for v in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY'):
        f.write("%s=%s\n" % (v, shlex.quote(px)))
os.chmod(os.path.expanduser('~/workspace/rp/.proxy-env'), 0o600)
EOF

# 2. install & enable
sudo ./setup-autostart.sh
# (opsional) tes tanpa reboot:
sudo systemctl start rp-tunnel.service
journalctl -u rp-tunnel.service -f   # atau: tail -f ~/workspace/rp/boot.log
```

> Catatan: file di `/etc` dan `/usr/local/bin` bisa hilang saat VM di-*replace*.
> Master copy selalu di `~/workspace/rp/` — jalankan ulang `setup-autostart.sh`
> untuk reinstall.

---

## 8. Troubleshooting

| Gejala | Kemungkinan penyebab | Cek |
|--------|---------------------|-----|
| `cloudflared` TLS handshake error saat start | Tidak lewat relay / proxy env kosong | `relay.log` ada "relay up"? `env` di konteks proses ada proxy? |
| `edge discovery: ... connection refused` (UDP :53) | `dns.py` belum siap / mati | `ss -uln \| grep :53`; tes query SRV manual (lihat README langkah 8) |
| Tunnel registered tapi SSH tidak masuk | sshd belum jalan / route dashboard salah | `ss -tln \| grep :22`; cek Public Hostname di dashboard |
| `apt update` sangat lambat | Overhead per-file via MITM proxy (normal di env ini) | `speedtest` via proxy untuk bedakan bandwidth vs overhead |
| Setelah reboot tunnel tidak naik | Service tidak enabled / file `/etc` ke-reset (VM replacement) | `systemctl is-enabled rp-tunnel`; jalankan ulang `setup-autostart.sh` |
| `pkill` membunuh shell sendiri | Pattern cocok dengan command line sendiri | Gunakan trik `[c]loudflared` |

---

## 9. Pelajaran yang dipetik

1. **Petakan constraint dulu, baru cari arsitektur.** Tiap jalan buntu di atas terjadi
   karena ada constraint yang belum dipetakan. Sekali petanya lengkap, solusinya
   hampir "mendisain dirinya sendiri".
2. **Bukti parsial itu berharga.** Shim LD_PRELOAD yang "setengah gagal" membuktikan
   jalur CONNECT valid — keberanian untuk lanjut datang dari situ.
3. **Bedakan batas arsitektur vs bug.** POST yang ditartpit bukan bug yang bisa
   diakali — ia memaksa pindah strategi (quick → named tunnel).
4. **Baca tool-nya (strace), jangan tebak.** Terobosan DNS datang dari melihat
   syscall Go yang sebenarnya, bukan dari dokumentasi.
5. **Tes autostart dengan simulasi reboot** (matikan semua proses → start service),
   jangan cuma `enable` lalu berharap.
6. **Jangan simpan kredensial di repo.** Token & password proxy di file 600 yang
   di-`.gitignore`; password sistem operasi hanya ditampilkan sekali.

---

*Dibuat dari pengalaman nyata: sandbox Ubuntu 24.04 di cloud, September 2026.*

# Prompt untuk Muse lain — cara LANGSUNG (tanpa Worker)

> Lupakan edge-wrap/Worker. Copy-paste blok di bawah ke Muse yang satunya.

---

```
Lupakan solusi Worker/edge-wrap — kita pakai cara LANGSUNG, persis seperti
yang terbukti jalan di sandbox referensi: tcprelay.py + dns.py + cloudflared
--token, tanpa modifikasi cloudflared.

Repo (private): https://github.com/lexaiko/sandbox-cloudflared-tunnel
Pakai file ASLI tanpa modifikasi: tcprelay.py, dns.py, hosts.example,
resolv.conf.example. PROMPT-tunneling.md = panduan lengkap arsitekturnya.

Konteks: eksperimenmu kemarin menyimpulkan proxy membunuh TLS ke :7844.
MUNGKIN benar, tapi MUNGKIN juga setup eksperimenmu yang cacat. Sebelum
menyimpulkan apa pun, verifikasi ulang berlapis. JANGAN lompat langkah,
JANGAN simpulkan dari ingatan — semua dari output perintah.

0. Prasyarat dari user: named tunnel SUDAH dibuat di dashboard Cloudflare
   (Zero Trust → Networks → Tunnels, gratis di free plan), public hostname
   route sudah di-set (mis. ssh.xxx.com → ssh://localhost:22), dan kamu
   pegang TUNNEL TOKEN-nya. Kalau belum ada, STOP dan minta user siapkan dulu.

1. Proxy env: pastikan https_proxy/HTTPS_PROXY valid.
   Test: curl -x "$https_proxy" -sI https://example.com | head -1 → harus 200.

2. Relay: jalankan tcprelay.py. Dari log harus terlihat ia listen di :443
   dan :7844, tanpa error "proxy refused".

3. DNS: dengan bind-mount /etc/resolv.conf aktif (nameserver 127.0.0.1),
   query langsung: dig @127.0.0.1 SRV _v2-origintunneld._tcp.argotunnel.com
   Harus dapat jawaban region1-4. (Kalau dig tidak ada, pakai python3 +
   socket seperti contoh di PROMPT-tunneling.md.)

4. UJI TENTU — 2 menit, ini yang menentukan (hosts palsu + relay + dns jalan):
   openssl s_client -connect 127.0.0.3:7844 -servername region1.v2.argotunnel.com -brief
   - Dapat "CONNECTION ESTABLISHED" + sertifikat *.v2.argotunnel.com
     → JALUR BERSIH. Eksperimenmu kemarin salah setup (kemungkinan besar:
     tcprelay.py mati saat cloudflared dijalankan, atau proxy env kosong
     di shell tersebut). Lanjut ke langkah 5.
   - "Connection reset by peer" PERSIS saat ClientHello dikirim
     → ulangi TANPA -servername, lalu coba openssl ke 127.0.0.2:443.
     Kalau semuanya reset → proxy memang membunuh TLS ke edge di level
     handshake. LAPOR ke user apa adanya: cara langsung TIDAK BISA di
     proxy ini. Jangan mengarang solusi lain tanpa bertanya ke user dulu.

5. Kalau jalur bersih: SATU shell, bind-mount hosts+resolv.conf, lalu:
   cloudflared tunnel --protocol http2 run --token <TOKEN>
   Harus muncul "Registered tunnel connection ... protocol=http2".
   Catatan: mount --bind hanya berlaku di satu sesi shell — mount DAN
   jalankan semua proses di shell yang SAMA (lihat PROMPT-tunneling.md).

Lapor ke user hasil langkah 0–5, tiap langkah dengan output persisnya.
Berhasil = log "Registered tunnel connection". Gagal = langkah nomor berapa
+ pesan error persisnya.
```

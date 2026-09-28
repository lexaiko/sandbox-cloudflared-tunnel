# Prompt untuk Muse lain — deploy Edge Wrap (solusi proxy ketat)

> Copy-paste seluruh isi blok di bawah ini ke Muse yang ada di mesin ber-proxy ketat.

---

```
Konteks: kamu sudah membuktikan lewat eksperimen — DNS SRV via dns.py ✅,
fake hosts 127.0.0.x ✅, tcprelay.py CONNECT ke proxy ✅ — tapi setiap TLS
handshake ke *.v2.argotunnel.com:7844 dibunuh proxy (connection reset by peer
tepat saat handshake: filtering SNI/ALPN). Arsitektur standar tidak bisa jalan
di proxy seketat ini. Itu kesimpulan yang valid, dan bukan salah setup-mu.

Solusi: EDGE-WRAP. Bungkus data plane cloudflared di dalam WebSocket (WSS)
ke Cloudflare Worker milik user. Proxy hanya melihat HTTPS biasa ke
<worker>.workers.dev:443 — tidak ada SNI argotunnel.com, tidak ada koneksi
ke :7844 yang terlihat. Worker-lah (dari jaringan Cloudflare, tanpa proxy)
yang membuka TCP mentah ke edge yang sebenarnya. cloudflared jalan TANPA
MODIFIKASI; tunnel, dashboard, dan ssh user@host tetap seperti biasa.

Repo (private): https://github.com/lexaiko/sandbox-cloudflared-tunnel
Fokus ke folder edge-wrap/:
- edge-wrap-worker.js — Worker: WSS in → TCP out ke edge
  (allowlist ketat *.v2.argotunnel.com:7844 + auth token, bukan open proxy)
- ws-edge-wrap.py — pengganti tcprelay.py untuk port 7844 (murni stdlib Python)
- edge-wrap/README.md — panduan deploy & pakai, BACA DULU sampai paham

Yang harus kamu lakukan, berurutan:

1. Pull repo, baca edge-wrap/README.md sampai paham arsitekturnya.
2. Deploy Worker — butuh bantuan user untuk langkah dashboard karena butuh
   akun Cloudflare mereka:
   a. Minta user konfirmasi: mereka punya/bersedia pakai Workers PAID plan
      (~$5/bulan)? WAJIB — API cloudflare:sockets hanya ada di paid plan.
      Kalau tidak, STOP dan lapor ke user, jangan lanjut.
   b. Pandu user: dashboard → Workers & Pages → Create Worker → Deploy →
      Edit code → paste seluruh edge-wrap-worker.js → Save and deploy.
   c. Minta user set secret WRAP_TOKEN di Settings → Variables and Secrets.
      Tokennya: minta user generate sendiri (mis. openssl rand -hex 32)
      ATAU kamu generate dan kasih ke user sekali saja.
   d. Minta hostname worker-nya (mis. edge-wrap.namakamu.workers.dev).
3. Di mesin (ganti peran tcprelay.py, sisanya SAMA seperti eksperimenmu):
   - dns.py + bind-mount /etc/hosts + /etc/resolv.conf: tetap seperti kemarin.
   - JANGAN jalankan tcprelay.py. Jalankan: WRAP_WORKER=<host> WRAP_TOKEN=<token>
     python3 ws-edge-wrap.py   (butuh proxy env https_proxy seperti biasa)
   - cloudflared tunnel --protocol http2 run --token <TOKEN_TUNNEL> (tanpa modifikasi)
4. Verifikasi:
   - Log ws-edge-wrap.py harus muncul: "wrap 127.0.0.x:7844 -> regionX... via worker ..."
   - Log cloudflared harus muncul: "Registered tunnel connection ... protocol=http2"
5. Kalau gagal, diagnosis berlapis (jangan tebak):
   - "proxy refused CONNECT to worker" → proxy juga memblokir workers.dev:443 (jarang).
   - "ws upgrade failed: 403" → WRAP_TOKEN salah / belum di-set di Worker.
   - cloudflared tetap "connection reset" → cek logs Worker di dashboard
     (Workers & Pages → worker → Logs): 403 = token, error bridge = masalah TCP.
6. Aturan keamanan: JANGAN ubah allowlist target di edge-wrap-worker.js tanpa
   persetujuan user. JANGAN commit WRAP_TOKEN / token tunnel ke repo.
   Kredensial hanya di env / secret, tidak di file yang di-push.

Lapor ke user: berhasil (bukti log) atau gagal di langkah nomor berapa
dengan pesan error persisnya.
```

# Edge Wrap — kalau proxy membunuh TLS langsung ke edge Cloudflare

Varian dari arsitektur utama di repo ini, untuk jaringan yang proxy-nya
**lebih ketat**: CONNECT ke proxy lolos, tapi TLS handshake ke
`*.v2.argotunnel.com:7844` langsung dibunuh (`connection reset by peer` /
`proxy closed` tepat saat handshake dimulai — biasanya karena filtering
berbasis SNI/ALPN).

## Idenya

Jangan biarkan proxy melihat TLS ke edge sama sekali. Bungkus data plane
tunnel di dalam **WebSocket (WSS)** ke **Cloudflare Worker** milikmu —
proxy hanya melihat HTTPS biasa ke `<worker>.workers.dev:443`.
Worker yang (dari jaringan Cloudflare, tanpa proxy) membuka TCP mentah
ke edge yang sebenarnya.

```
[mesin] cloudflared ──TCP──▶ 127.0.0.x:7844
                                │  (hosts palsu + dns.py: SAMA seperti arsitektur utama)
                                ▼
                        ws-edge-wrap.py
                                │  WSS ke worker.workers.dev:443
                                │  (proxy cuma lihat HTTPS biasa)
                                ▼
                        ┌──────────────────┐
                        │ Cloudflare Worker │──TCP──▶ regionX.v2.argotunnel.com:7844
                        └──────────────────┘         (data plane tunnel ASLI)
```

**cloudflared tidak dimodifikasi sama sekali** — tunnel, dashboard, public
hostname, dan `ssh user@host` tetap jalan persis seperti biasa. Yang diganti
hanya transport-nya: `ws-edge-wrap.py` menggantikan peran `tcprelay.py`
untuk port 7844.

## Syarat

- **Cloudflare Workers Paid plan** (~$5/bulan) — API TCP sockets
  (`cloudflare:sockets`) hanya tersedia di paid plan.
- Token tunnel (named tunnel) seperti biasa.

## Deploy (sekali saja)

1. Dashboard Cloudflare → **Workers & Pages** → **Create Worker** → **Deploy**.
2. **Edit code** → hapus isi default → paste seluruh `edge-wrap-worker.js` → **Save and deploy**.
3. **Settings → Variables and Secrets** → tambah Secret:
   - Name: `WRAP_TOKEN`, Value: string acak yang kuat (mis. hasil `openssl rand -hex 32`).
4. Catat hostname worker-mu, mis. `edge-wrap.namakamu.workers.dev`.
5. (Opsional tapi disarankan) batasi Worker hanya menerima dari jaringanmu —
   Worker ini sudah punya allowlist target (`*.v2.argotunnel.com:7844` saja)
   dan butuh token, jadi bukan open proxy.

## Pakai di mesin

```bash
# ganti tcprelay.py dengan ws-edge-wrap.py untuk port 7844.
# dns.py + /etc/hosts + /etc/resolv.conf tetap SAMA seperti arsitektur utama.
export WRAP_WORKER='edge-wrap.namakamu.workers.dev'
export WRAP_TOKEN='<token-yang-sama-dengan-di-worker>'
# proxy env seperti biasa (https_proxy / HTTPS_PROXY)
python3 ws-edge-wrap.py &
python3 ../dns.py &   # atau dari ~/workspace/rp/
cloudflared tunnel --protocol http2 run --token '<TUNNEL_TOKEN>'
```

Semua dalam satu mount namespace seperti biasa (bind-mount hosts + resolv.conf
dulu dalam shell yang sama) — lihat README utama bagian Instalasi.

## Verifikasi

- Log `ws-edge-wrap.py`: harus muncul `wrap 127.0.0.x:7844 -> regionX... via worker ...`
- Log cloudflared: `Registered tunnel connection ... protocol=http2`
- Kalau `ws upgrade failed: 403` → token salah. Kalau `proxy refused CONNECT` →
  proxy juga memblokir `workers.dev` (sangat jarang).

## Batasan

- Latensi nambah satu hop (mesin → Worker → edge). Untuk SSH interaktif tetap nyaman.
- Butuh Workers Paid. Kalau tidak ada budget, alternatifnya adalah web terminal
  (Worker serve xterm.js + WebSocket langsung ke sshd) — lebih simpel tapi
  mengubah cara akses (browser, bukan `ssh` CLI).

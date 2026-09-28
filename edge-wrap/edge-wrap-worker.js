// edge-wrap-worker.js — Cloudflare Worker: WebSocket in, raw TCP out.
//
// Untuk mesin yang proxy-nya membunuh TLS langsung ke *.v2.argotunnel.com:7844:
// mesin menyambung WSS (HTTPS biasa, membosankan bagi proxy) ke Worker ini,
// Worker membuka TCP mentah ke edge Cloudflare yang sebenarnya.
//
// BUTUH: Workers Paid plan (cloudflare:sockets hanya tersedia di paid plan).
// Deploy: dashboard Cloudflare → Workers & Pages → Create Worker → paste file ini
// → Settings → Variables → tambah Secret WRAP_TOKEN → Deploy.
//
// Cara pakai (lihat ws-edge-wrap.py):
//   WSS ke https://<worker>.workers.dev/edge/<host>/<port>?token=<WRAP_TOKEN>
//   Host yang diizinkan dibatasi ke *.v2.argotunnel.com:7844 (allowlist di bawah).

import { connect } from 'cloudflare:sockets';

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const m = url.pathname.match(/^\/edge\/([A-Za-z0-9.-]+)\/(\d+)$/);
    if (!m) return new Response('edge-wrap ok — use /edge/<host>/<port>?token=...\n', { status: 200 });

    if (!env.WRAP_TOKEN || url.searchParams.get('token') !== env.WRAP_TOKEN)
      return new Response('forbidden\n', { status: 403 });

    const host = m[1];
    const port = parseInt(m[2], 10);
    // Allowlist ketat: hanya edge tunnel Cloudflare, hanya port 7844.
    // Worker ini JANGAN jadi open proxy.
    if (!/^[a-z0-9.-]+\.v2\.argotunnel\.com$/.test(host) || port !== 7844)
      return new Response('target not allowed\n', { status: 403 });

    if (request.headers.get('Upgrade') !== 'websocket')
      return new Response('websocket required\n', { status: 426 });

    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    server.accept();
    bridge(server, host, port).catch(() => {
      try { server.close(1011, 'bridge error'); } catch (e) {}
    });
    return new Response(null, { status: 101, webSocket: client });
  },
};

async function bridge(ws, host, port) {
  const sock = connect({ hostname: host, port });
  await sock.opened;

  const writer = sock.writable.getWriter();
  const reader = sock.readable.getReader();
  let closed = false;

  const closeAll = () => {
    if (closed) return;
    closed = true;
    try { ws.close(); } catch (e) {}
    reader.cancel().catch(() => {});
    try { writer.releaseLock(); } catch (e) {}
    sock.close().catch(() => {});
  };

  ws.addEventListener('message', async (ev) => {
    try {
      const data = typeof ev.data === 'string'
        ? new TextEncoder().encode(ev.data)
        : new Uint8Array(await ev.data.arrayBuffer());
      await writer.write(data);
    } catch (e) { closeAll(); }
  });
  ws.addEventListener('close', closeAll);
  ws.addEventListener('error', closeAll);

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done || closed) break;
      ws.send(value); // Uint8Array → binary WS frame
    }
  } catch (e) {}
  closeAll();
}

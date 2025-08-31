import express from 'express';
import cors from 'cors';
import bodyParser from 'body-parser';
import { pool } from './db.js';
import { WebSocketServer } from 'ws';
import http from 'http';
import 'dotenv/config';

const app = express();
app.use(cors());
app.use(bodyParser.json({ limit: '2mb' }));

// health
app.get('/health', (_, res) => res.json({ ok: true }));

// debug helpers
app.get('/api/debug/users', async (_, res) => {
  const r = await pool.query('SELECT pubkey, nickname, created_at FROM users ORDER BY created_at DESC');
  res.json(r.rows);
});
app.get('/api/debug/friendships', async (_, res) => {
  const r = await pool.query('SELECT host_pubkey, friend_pubkey, status, permissions, created_at FROM friendships ORDER BY created_at DESC');
  res.json(r.rows);
});

// register
app.post('/api/register', async (req, res) => {
  const { pubkey, nickname } = req.body || {};
  if (!pubkey) return res.status(400).json({ error: 'pubkey required' });
  await pool.query(
    'INSERT INTO users (pubkey, nickname) VALUES ($1, $2) ON CONFLICT (pubkey) DO UPDATE SET nickname=EXCLUDED.nickname',
    [pubkey, nickname || null]
  );
  res.json({ ok: true });
});

// friend request (viewer -> host)
app.post('/api/friends/request', async (req, res) => {
  const { me, friend } = req.body || {};
  if (!me || !friend) return res.status(400).json({ error: 'me and friend required' });
  await pool.query(
    'INSERT INTO friendships (host_pubkey, friend_pubkey, status, permissions) VALUES ($1,$2,$3,$4) ON CONFLICT (host_pubkey, friend_pubkey) DO NOTHING',
    [friend, me, 'pending', JSON.stringify({})]
  );
  res.json({ ok: true });
});

// accept (both directions)
app.post('/api/friends/accept', async (req, res) => {
  const { me, friend } = req.body || {};
  if (!me || !friend) return res.status(400).json({ error: 'me and friend required' });
  await pool.query(
    'INSERT INTO friendships (host_pubkey, friend_pubkey, status, permissions) VALUES ($1,$2,$3,$4) ON CONFLICT (host_pubkey, friend_pubkey) DO UPDATE SET status=$3',
    [me, friend, 'accepted', JSON.stringify({})]
  );
  await pool.query(
    'INSERT INTO friendships (host_pubkey, friend_pubkey, status, permissions) VALUES ($1,$2,$3,$4) ON CONFLICT (host_pubkey, friend_pubkey) DO UPDATE SET status=$3',
    [friend, me, 'accepted', JSON.stringify({})]
  );
  res.json({ ok: true });
});

// set permissions
app.post('/api/friends/permissions', async (req, res) => {
  const { host, friend, permissions } = req.body || {};
  if (!host || !friend || !permissions) return res.status(400).json({ error: 'host, friend, permissions required' });
  await pool.query('UPDATE friendships SET permissions=$3 WHERE host_pubkey=$1 AND friend_pubkey=$2',
    [host, friend, JSON.stringify(permissions)]);
  res.json({ ok: true });
});

// get permissions (accept both querystring and JSON body; handles +, / safely)
app.all('/api/friends/permissions', async (req, res) => {
  const q = req.query || {};
  const b = (req.body && typeof req.body === 'object') ? req.body : {};
  const host = (q.host || b.host || '').toString();
  const friend = (q.friend || b.friend || '').toString();
  if (!host || !friend) return res.status(400).json({ error: 'host and friend required' });
  const r = await pool.query('SELECT status, permissions FROM friendships WHERE host_pubkey=$1 AND friend_pubkey=$2', [host, friend]);
  if (r.rowCount === 0) return res.status(404).json({ error: 'not found' });
  res.json(r.rows[0]);
});

// one-shot upsert (makes row (host, friend) = accepted w/ perms, and (friend, host) accepted)
app.post('/api/friends/upsert', async (req, res) => {
  const { host, friend, permissions } = req.body || {};
  if (!host || !friend) return res.status(400).json({ error: 'host and friend required' });
  const perms = permissions || {};
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query('INSERT INTO users (pubkey) VALUES ($1) ON CONFLICT DO NOTHING', [host]);
    await client.query('INSERT INTO users (pubkey) VALUES ($1) ON CONFLICT DO NOTHING', [friend]);
    await client.query(
      'INSERT INTO friendships (host_pubkey, friend_pubkey, status, permissions) VALUES ($1,$2,$3,$4) ' +
      'ON CONFLICT (host_pubkey, friend_pubkey) DO UPDATE SET status=$3, permissions=$4',
      [host, friend, 'accepted', JSON.stringify(perms)]
    );
    await client.query(
      'INSERT INTO friendships (host_pubkey, friend_pubkey, status, permissions) VALUES ($1,$2,$3,$4) ' +
      'ON CONFLICT (host_pubkey, friend_pubkey) DO UPDATE SET status=$3',
      [friend, host, 'accepted', JSON.stringify({})]
    );
    await client.query('COMMIT');
    res.json({ ok: true });
  } catch (e) {
    await client.query('ROLLBACK');
    console.error('[upsert ERROR]', e);
    res.status(500).json({ error: 'db error' });
  } finally {
    client.release();
  }
});

// ---- WS signaling ----
const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: '/ws' });

const clients = new Map();
const send = (ws, obj) => { try { ws.send(JSON.stringify(obj)); } catch {} };

wss.on('connection', (ws, req) => {
  const url = new URL(req.url, 'http://x');
  const pubkey = url.searchParams.get('pubkey');
  if (!pubkey) { ws.close(1008, 'pubkey required'); return; }
  clients.set(pubkey, ws);
  console.log('[ws] connected', pubkey.slice(0,8), '…');
  ws.on('close', () => { clients.delete(pubkey); console.log('[ws] closed', pubkey.slice(0,8), '…'); });

  ws.on('message', async (raw) => {
    let data; try { data = JSON.parse(raw.toString()); } catch { return; }

    if (data.type === 'join-request') {
      const { host, viewer } = data;
      console.log('[join-request]', { host, viewer });
      try {
        const r = await pool.query('SELECT status, permissions FROM friendships WHERE host_pubkey=$1 AND friend_pubkey=$2', [host, viewer]);
        if (r.rowCount === 0) { console.log('[join-denied] not-friends (no row)'); send(ws, { type:'join-denied', reason:'not-friends' }); return; }
        const row = r.rows[0];
        if (row.status !== 'accepted') { console.log('[join-denied] not-friends (status=', row.status, ')'); send(ws, { type:'join-denied', reason:'not-friends' }); return; }
        const perms = row.permissions || {};
        const hostWS = clients.get(host);
        if (!hostWS) { console.log('[join-denied] host-offline'); send(ws, { type:'join-denied', reason:'host-offline' }); return; }
        console.log('[incoming-join] notify host, perms=', perms);
        send(hostWS, { type:'incoming-join', viewer, permissions: perms });
      } catch (e) {
        console.error('[join-request ERROR]', e);
      }
      return;
    }

    if (['offer', 'answer', 'ice', 'input-permissions'].includes(data.type)) {
      const target = clients.get(data.to);
      if (target) send(target, data);
      return;
    }
  });

  send(ws, { type: 'hello', you: pubkey });
});

const PORT = process.env.PORT || 8080;
server.listen(PORT, () => console.log('Backend listening on', PORT));


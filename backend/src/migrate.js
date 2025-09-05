import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { Pool } from 'pg';
import 'dotenv/config';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const cfgPath =
  process.env.LILIUM_NETCFG ||
  path.resolve(process.cwd(), 'network_config.json') ||
  path.resolve(__dirname, '../network_config.json');

let NETCFG = {};
try {
  NETCFG = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
} catch {
  NETCFG = {};
}

const dbUrl = process.env.DATABASE_URL || (NETCFG.database && NETCFG.database.url);
const pool = new Pool({ connectionString: dbUrl });

const sql = `
CREATE TABLE IF NOT EXISTS users (
  pubkey TEXT PRIMARY KEY,
  nickname TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS friendships (
  host_pubkey   TEXT NOT NULL,
  friend_pubkey TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'accepted'
  permissions   JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMP DEFAULT now(),
  PRIMARY KEY (host_pubkey, friend_pubkey),
  FOREIGN KEY (host_pubkey)  REFERENCES users(pubkey) ON DELETE CASCADE,
  FOREIGN KEY (friend_pubkey) REFERENCES users(pubkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS connkeys (
  host_pubkey   TEXT NOT NULL,
  friend_pubkey TEXT NOT NULL,
  conn_key      TEXT NOT NULL,
  created_at    TIMESTAMP DEFAULT now(),
  PRIMARY KEY (host_pubkey, friend_pubkey),
  FOREIGN KEY (host_pubkey)  REFERENCES users(pubkey) ON DELETE CASCADE,
  FOREIGN KEY (friend_pubkey) REFERENCES users(pubkey) ON DELETE CASCADE
);
`;

(async () => {
  const client = await pool.connect();
  try {
    await client.query(sql);
    console.log("Migration completed");
  } catch (e) {
    console.error(e);
    process.exit(1);
  } finally {
    client.release();
    process.exit(0);
  }
})();

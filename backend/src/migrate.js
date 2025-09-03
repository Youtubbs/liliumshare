import { Pool } from 'pg';
import 'dotenv/config';
const pool = new Pool({ connectionString: process.env.DATABASE_URL });

const sql = `
CREATE TABLE IF NOT EXISTS users (
  pubkey TEXT PRIMARY KEY,
  nickname TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS friendships (
  host_pubkey TEXT NOT NULL,
  friend_pubkey TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'accepted'
  permissions JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMP DEFAULT now(),
  PRIMARY KEY (host_pubkey, friend_pubkey),
  FOREIGN KEY (host_pubkey) REFERENCES users(pubkey) ON DELETE CASCADE,
  FOREIGN KEY (friend_pubkey) REFERENCES users(pubkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS connkeys (
  host_pubkey   TEXT NOT NULL,
  friend_pubkey TEXT NOT NULL,
  conn_key      TEXT NOT NULL,
  created_at    TIMESTAMP DEFAULT now(),
  PRIMARY KEY (host_pubkey, friend_pubkey),
  FOREIGN KEY (host_pubkey) REFERENCES users(pubkey) ON DELETE CASCADE,
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

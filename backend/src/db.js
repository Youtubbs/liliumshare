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

function fromDbPartsEnv() {
  const host = process.env.DB_HOST;
  const port = process.env.DB_PORT;
  const user = process.env.DB_USER;
  const pass = process.env.DB_PASSWORD;
  const name = process.env.DB_NAME;
  if (host && port && user && pass && name) {
    const enc = encodeURIComponent;
    return `postgres://${enc(user)}:${enc(pass)}@${host}:${port}/${enc(name)}`;
  }
  return null;
}

const dbUrl =
  process.env.DATABASE_URL ||
  (NETCFG.database && NETCFG.database.url) ||
  fromDbPartsEnv();

if (!dbUrl) {
  console.warn(
    '[db] No DATABASE_URL/DB_* env or NETCFG.database.url found. Default libpq environment will be used (likely fails).'
  );
}

export const pool = new Pool(
  dbUrl ? { connectionString: dbUrl } : undefined
);

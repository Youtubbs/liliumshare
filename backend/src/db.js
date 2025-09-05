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

export const pool = new Pool({
  connectionString: dbUrl,
});
import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";

const dbPath = path.resolve(process.env.DATABASE_PATH ?? "./data/delta-desk.sqlite");
fs.mkdirSync(path.dirname(dbPath), { recursive: true });

const globalForDb = globalThis as unknown as { deltaDeskDb?: Database.Database };
export const db = globalForDb.deltaDeskDb ?? new Database(dbPath);
if (process.env.NODE_ENV !== "production") globalForDb.deltaDeskDb = db;

db.pragma("journal_mode = WAL");
db.pragma("foreign_keys = ON");
db.exec(`
  CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    api_key TEXT NOT NULL UNIQUE,
    secret_ciphertext TEXT NOT NULL,
    secret_iv TEXT NOT NULL,
    secret_tag TEXT NOT NULL,
    environment TEXT NOT NULL CHECK(environment IN ('production','testnet')),
    delta_user_id TEXT,
    account_name TEXT,
    email_masked TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
  CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    entry_at TEXT,
    exit_at TEXT,
    entry_execution_at TEXT,
    exit_execution_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
  );
  CREATE INDEX IF NOT EXISTS strategies_due_idx ON strategies(status, entry_at, exit_at);
  CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('entry','exit')),
    status TEXT NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT
  );
  CREATE TABLE IF NOT EXISTS execution_orders (
    id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    leg_id TEXT NOT NULL,
    delta_order_id TEXT,
    client_order_id TEXT NOT NULL UNIQUE,
    product_id INTEGER NOT NULL,
    product_symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    size INTEGER NOT NULL,
    state TEXT NOT NULL,
    response_json TEXT,
    created_at TEXT NOT NULL
  );
`);

export type AccountRow = {
  id: string;
  api_key: string;
  secret_ciphertext: string;
  secret_iv: string;
  secret_tag: string;
  environment: "production" | "testnet";
  delta_user_id: string | null;
  account_name: string | null;
  email_masked: string | null;
};

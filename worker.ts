import { processDueStrategies } from "./lib/engine";

const pollMs = Math.max(1000, Number(process.env.WORKER_POLL_MS ?? 2000));
console.log(`Delta strategy worker started (polling every ${pollMs}ms)`);

let running = false;
setInterval(async () => {
  if (running) return;
  running = true;
  try { await processDueStrategies(); } finally { running = false; }
}, pollMs);

await processDueStrategies();

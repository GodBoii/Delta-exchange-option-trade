"use client";

import { useEffect, useState } from "react";
import { ConvexHttpClient } from "convex/browser";
import { makeFunctionReference } from "convex/server";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { formatDateTime } from "@/lib/format";
import { EmptyState, Panel, PanelHeader } from "@/app/components/ui";
import { Activity } from "@/app/components/icons";

type MirroredRun = {
  id: string; name: string; status: string; entryAt: string | null;
  exitAt: string | null; updatedAt: number;
};

const readOnly = makeFunctionReference<"query", { entityType: "strategies" }, Array<{
  summary: unknown; updatedAt: number;
}>>("recovery:readOnlyForUser");

function parseRun(value: unknown, updatedAt: number): MirroredRun | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  if (!("id" in value) || typeof value.id !== "string" ||
      !("name" in value) || typeof value.name !== "string" ||
      !("status" in value) || typeof value.status !== "string") return null;
  const entryAt = "entry_at" in value && typeof value.entry_at === "string" ? value.entry_at : null;
  const exitAt = "exit_at" in value && typeof value.exit_at === "string" ? value.exit_at : null;
  return { id: value.id, name: value.name, status: value.status, entryAt, exitAt, updatedAt };
}

export default function RecoveryHistory() {
  const [runs, setRuns] = useState<MirroredRun[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "unavailable">("loading");

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const url = process.env.NEXT_PUBLIC_CONVEX_URL;
        if (!url) throw new Error("Recovery copy unavailable");
        const { data: { session } } = await getSupabaseBrowserClient().auth.getSession();
        if (!session) throw new Error("Sign in required");
        const client = new ConvexHttpClient(url);
        client.setAuth(session.access_token);
        const items = await client.query(readOnly, { entityType: "strategies" });
        if (cancelled) return;
        setRuns(items.map(item => parseRun(item.summary, item.updatedAt))
          .filter((row): row is MirroredRun => row !== null)
          .sort((left, right) => right.updatedAt - left.updatedAt));
        setState("ready");
      } catch {
        if (!cancelled) setState("unavailable");
      }
    };
    void load();
    return () => { cancelled = true; };
  }, []);

  const lastSaved = runs.length ? Math.max(...runs.map(run => run.updatedAt)) : null;
  return (
    <Panel>
      <PanelHeader icon={<Activity />} title="Saved trade state" />
      <p role="status" className="library-state tone-warning">
        {lastSaved !== null
          ? `Trading services are offline. This read-only recovery copy was last updated ${new Date(lastSaved).toLocaleString()}.`
          : "Trading services are offline. This recovery copy is read-only."}
      </p>
      {state === "loading" && <p>Loading the recovery copy…</p>}
      {state === "unavailable" && <EmptyState icon={<Activity />} title="Recovery copy unavailable" description="Try again after reconnecting." />}
      {state === "ready" && runs.length === 0 &&
        <EmptyState icon={<Activity />} title="No saved trade state" description="No mirrored strategies are available for this account." />}
      {state === "ready" && runs.length > 0 && (
        <div className="table-scroll">
          <table>
            <thead><tr><th scope="col">Strategy</th><th scope="col">Status</th><th scope="col">Entry</th><th scope="col">Exit</th></tr></thead>
            <tbody>{runs.map(run => (
              <tr key={run.id}>
                <th scope="row">{run.name}</th>
                <td>{run.status.replaceAll("_", " ")}</td>
                <td>{run.entryAt ? formatDateTime(run.entryAt) : "—"}</td>
                <td>{run.exitAt ? formatDateTime(run.exitAt) : "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

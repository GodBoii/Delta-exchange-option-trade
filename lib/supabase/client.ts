import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { Database } from "@/lib/supabase/types";

let browserClient: SupabaseClient<Database> | undefined;

export function getSupabaseBrowserClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) throw new Error("Supabase public environment variables are not configured");
  // @supabase/ssr 0.6 and newer @supabase/supabase-js releases expose different
  // generic parameter lists, although the runtime client is the same object.
  browserClient ??= createBrowserClient<Database>(url, key) as unknown as SupabaseClient<Database>;
  return browserClient;
}

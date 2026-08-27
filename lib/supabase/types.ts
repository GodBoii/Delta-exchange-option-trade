export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[];

type Table<Row, Insert = Partial<Row>, Update = Partial<Insert>> = {
  Row: Row;
  Insert: Insert;
  Update: Update;
  Relationships: [];
};

export type ProfileRow = { id: string; display_name: string | null; avatar_url: string | null; phone_number: string | null; created_at: string; updated_at: string };
export type ConnectionRow = { id: string; user_id: string; provider: string; api_key: string; vault_secret_id: string; environment: "production"; delta_user_id: string | null; account_name: string | null; email_masked: string | null; status: string; verified_at: string; created_at: string; updated_at: string };
export type SavedStrategyRow = { id: string; user_id: string | null; name: string; definition_json: Json; source_run_id: string | null; version: number; enabled_for_ai: boolean; is_default?: boolean; created_at: string; updated_at: string };
export type StrategyRow = { id: string; user_id: string; saved_strategy_id: string | null; name: string; status: string; definition_json: Json; entry_at: string | null; exit_at: string | null; entry_execution_at: string | null; exit_execution_at: string | null; capital_slot: number | null; capital_budget: number | null; capital_policy_json: Json; last_error: string | null; created_at: string; updated_at: string };
export type ExecutionRow = { id: string; strategy_id: string; kind: "entry" | "exit"; status: string; error: string | null; started_at: string; completed_at: string | null };
export type ExecutionOrderRow = { id: string; execution_id: string; leg_id: string; delta_order_id: string | null; client_order_id: string; product_id: number; product_symbol: string; side: "buy" | "sell"; size: number; state: string; response_json: Json | null; created_at: string };
export type CapitalSettingsRow = { user_id: string; allocation_mode: string; capital_amount: number | null; created_at: string; updated_at: string };

export type Database = {
  public: {
    Tables: {
      profiles: Table<ProfileRow, { id: string; display_name?: string | null; avatar_url?: string | null; phone_number?: string | null }, { display_name?: string | null; avatar_url?: string | null; phone_number?: string | null; updated_at?: string }>;
      exchange_connections: Table<ConnectionRow, Partial<ConnectionRow> & { user_id: string; api_key: string; vault_secret_id: string }, Partial<ConnectionRow>>;
      saved_strategies: Table<SavedStrategyRow, { user_id: string; name: string; definition_json: Json; source_run_id?: string | null; enabled_for_ai?: boolean }, Partial<SavedStrategyRow>>;
      strategies: Table<StrategyRow, { user_id: string; saved_strategy_id?: string | null; name: string; status: string; definition_json: Json; entry_at?: string | null; exit_at?: string | null }, Partial<StrategyRow>>;
      executions: Table<ExecutionRow, { strategy_id: string; kind: "entry" | "exit"; status: string; error?: string | null; started_at?: string; completed_at?: string | null }, Partial<ExecutionRow>>;
      execution_orders: Table<ExecutionOrderRow, { execution_id: string; leg_id: string; delta_order_id?: string | null; client_order_id: string; product_id: number; product_symbol: string; side: "buy" | "sell"; size: number; state: string; response_json?: Json | null }, Partial<ExecutionOrderRow>>;
      capital_settings: Table<CapitalSettingsRow, { user_id: string; allocation_mode?: string; capital_amount?: number | null }, Partial<CapitalSettingsRow>>;
    };
    Views: { [_ in never]: never };
    Functions: {
      store_delta_connection: { Args: { p_user_id: string; p_api_key: string; p_api_secret: string; p_delta_user_id: string; p_account_name: string; p_email_masked: string | null }; Returns: string };
      get_delta_credentials: { Args: { p_user_id: string }; Returns: { connection_id: string; api_key: string; api_secret: string; environment: "production"; delta_user_id: string | null; account_name: string | null; email_masked: string | null; status: string }[] };
      delete_delta_connection: { Args: { p_user_id: string }; Returns: boolean };
    };
    Enums: Record<string, never>;
    CompositeTypes: Record<string, never>;
  };
};

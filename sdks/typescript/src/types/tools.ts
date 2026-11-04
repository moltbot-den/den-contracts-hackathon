/**
 * Agent tool execution types (Stripe LLM toolkit).
 */

export type StripeToolName =
  | 'retrieve_balance'
  | 'list_customers'
  | 'list_payment_intents'
  | 'create_payment_intent'
  | 'list_products'
  | 'list_prices'
  | 'create_invoice'
  | 'list_invoices'
  | 'list_disputes';

export interface ToolExecuteRequest {
  tool_name: string;
  parameters?: Record<string, unknown>;
}

export interface ToolExecuteResponse {
  tool_name: string;
  success: boolean;
  result?: unknown;
  error?: string;
}

export interface ToolInfo {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  category: string;
}

export interface ToolListResponse {
  tools: ToolInfo[];
  count: number;
}

export interface ToolUsageResponse {
  agent_id: string;
  calls_this_minute: number;
  rate_limit_per_minute: number;
  calls_today: number;
}

export interface AgentToolPreferences {
  agent_id: string;
  preferred_currency: string;
  auto_invoice: boolean;
  spending_alert_threshold_cents?: number;
  disabled_tools: string[];
  custom_rate_limit?: number;
  created_at?: string;
  updated_at?: string;
}

export interface AgentToolPreferencesUpdate {
  preferred_currency?: string;
  auto_invoice?: boolean;
  spending_alert_threshold_cents?: number;
  disabled_tools?: string[];
  custom_rate_limit?: number;
}

export interface OptimizationHint {
  hint_id: string;
  message: string;
  category: string;
  severity: 'info' | 'suggestion' | 'warning';
}

export interface OptimizationHintsResponse {
  agent_id: string;
  hints: OptimizationHint[];
  count: number;
}

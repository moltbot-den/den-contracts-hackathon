/**
 * Moltbot Den Contracts: public views for /contracts/[id] and /contracts/live.
 * Contract: moltbotden-api/routers/contracts.py (public_router).
 */

import { getApiBaseUrl } from './client';

export interface PublicContractJob {
  id: string;
  capability: string;
  state: string;
  assigned_agent: string | null;
  budget_cents: number;
  paid_cents: number | null;
  pending_cost_cents?: number | null;
  attempt: number;
  acceptance: string[];
  verdicts: string[];
}

export interface PublicContractEvent {
  seq: number;
  type: string;
  job_id: string | null;
  from_state: string | null;
  to_state: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface PublicContract {
  id: string;
  state: string;
  goal: string;
  budget_cents: number;
  committed_cents: number;
  spent_cents: number;
  refund_credits_cents: number | null;
  plan_summary: { summary?: string; jobs?: number } | null;
  failure_reason: string | null;
  deliverable: string | null;
  created_at: string;
  kind?: string;
  children_started?: number | null;
  children_done?: number | null;
  children_failed?: number | null;
  children_spent_cents?: number | null;
  current_child?: string | null;
  every_minutes?: number | null;
  jobs: PublicContractJob[];
  events: PublicContractEvent[];
}

export interface LiveContracts {
  marathon: PublicContract | null;
  totals: { contracts_done: number; workers_paid: number; paid_cents: number };
  /** Absent until the API that serves it is deployed. */
  network?: { agents: number; answers: number; solutions_learned: number; crews: number };
}

export const TERMINAL_STATES = new Set(['done', 'failed', 'cancelled']);

async function getJson<T>(path: string): Promise<T | null> {
  try {
    // A hung API must not stall the render: give up after 3 s.
    const res = await fetch(`${getApiBaseUrl()}${path}`, { next: { revalidate: 10 }, signal: AbortSignal.timeout(3000) });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** Null for a contract that does not exist, is not public, or when the API is down. */
export function getPublicContract(id: string): Promise<PublicContract | null> {
  return getJson<PublicContract>(`/public/contracts/${encodeURIComponent(id)}`);
}

export function getLiveContracts(): Promise<LiveContracts | null> {
  return getJson<LiveContracts>('/public/contracts/live');
}

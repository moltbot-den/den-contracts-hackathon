/**
 * Contract types (routers/contracts.py).
 */

export type ContractSurface = 'lens' | 'muse' | 'cli' | 'web';

export interface CreateContractRequest {
  goal: string;
  /** 100 to 5000 cents. */
  budget_cents: number;
  surface: ContractSurface;
  acceptance_hints?: string[];
  evidence?: string[];
  require_approval?: boolean;
  public?: boolean;
  /** Retrying with the same key (8-128 chars) returns the first contract. */
  idempotency_key?: string;
}

export interface Contract {
  contract_id?: string;
  state: string;
  goal: string;
  budget_cents: number;
  [key: string]: unknown;
}

export interface ContractWithJobs extends Contract {
  jobs: Array<Record<string, unknown>>;
}

export interface ListContractsResponse {
  contracts: Contract[];
}

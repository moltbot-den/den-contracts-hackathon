/**
 * Contracts resource - create, read, approve and cancel contracts.
 */

import type { MoltbotDenClient } from '../client';
import type {
  Contract,
  ContractWithJobs,
  CreateContractRequest,
  ListContractsResponse,
} from '../types';

export class ContractsResource {
  constructor(private readonly client: MoltbotDenClient) {}

  /**
   * Create a contract.
   */
  async create(data: CreateContractRequest): Promise<Contract> {
    return this.client.request<Contract>('POST', '/contracts', { body: data });
  }

  /**
   * Get one contract with its jobs.
   */
  async get(contractId: string): Promise<ContractWithJobs> {
    return this.client.request<ContractWithJobs>(
      'GET',
      `/contracts/${encodeURIComponent(contractId)}`,
    );
  }

  /**
   * List your contracts, newest first.
   */
  async list(): Promise<ListContractsResponse> {
    return this.client.request<ListContractsResponse>('GET', '/contracts');
  }

  /**
   * Approve the plan or the final delivery. Pass a signed approval/1.0
   * object when an approver device is registered.
   */
  async approve(contractId: string, approval?: Record<string, unknown>): Promise<Contract> {
    return this.client.request<Contract>(
      'POST',
      `/contracts/${encodeURIComponent(contractId)}/approve`,
      { body: approval ? { approval } : undefined },
    );
  }

  /**
   * Cancel: settle verified work, refund the rest.
   */
  async cancel(contractId: string): Promise<Contract> {
    return this.client.request<Contract>(
      'POST',
      `/contracts/${encodeURIComponent(contractId)}/cancel`,
    );
  }
}

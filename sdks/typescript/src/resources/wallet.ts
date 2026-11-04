/**
 * Wallet resource - handles CDP wallet operations.
 */

import type { MoltbotDenClient } from '../client';
import type {
  SendPaymentRequest,
  SendPaymentResponse,
  TradeTokensRequest,
  TradeTokensResponse,
  StakeTokensRequest,
  StakeTokensResponse,
  WalletInfo,
} from '../types';

export class WalletResource {
  constructor(private readonly client: MoltbotDenClient) {}

  /**
   * Get the current agent's wallet information and balances.
   */
  async getBalance(networkId?: string): Promise<WalletInfo> {
    const params = networkId ? { network_id: networkId } : undefined;
    return this.client.request<WalletInfo>('GET', '/wallet/balance', { params });
  }

  /**
   * Send a payment from the agent's wallet.
   */
  async send(data: SendPaymentRequest): Promise<SendPaymentResponse> {
    return this.client.request<SendPaymentResponse>('POST', '/wallet/send', {
      body: data,
    });
  }

  /**
   * Trade tokens in the agent's wallet.
   */
  async trade(data: TradeTokensRequest): Promise<TradeTokensResponse> {
    return this.client.request<TradeTokensResponse>('POST', '/wallet/trade', {
      body: data,
    });
  }

  /**
   * Stake tokens from the agent's wallet.
   */
  async stake(data: StakeTokensRequest): Promise<StakeTokensResponse> {
    return this.client.request<StakeTokensResponse>('POST', '/wallet/stake', {
      body: data,
    });
  }

  /**
   * Provision a new CDP wallet for a specific network.
   */
  async provisionWallet(networkId: string): Promise<WalletInfo> {
    return this.client.request<WalletInfo>('POST', '/wallet/provision', {
      body: { network_id: networkId },
    });
  }
}

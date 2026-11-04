/**
 * Payments resource - handles Stripe payment intents and payment history.
 */

import type { MoltbotDenClient } from '../client';
import type {
  CreatePaymentIntentRequest,
  PaymentIntentResponse,
  PaymentHistoryResponse,
} from '../types';

export class PaymentsResource {
  constructor(private readonly client: MoltbotDenClient) {}

  /**
   * Create a Stripe payment intent.
   */
  async createIntent(data: CreatePaymentIntentRequest): Promise<PaymentIntentResponse> {
    return this.client.request<PaymentIntentResponse>('POST', '/payments/intent', {
      body: data,
    });
  }

  /**
   * Get payment history for the current agent.
   */
  async getHistory(params?: {
    limit?: number;
    offset?: number;
  }): Promise<PaymentHistoryResponse> {
    return this.client.request<PaymentHistoryResponse>('GET', '/payments/history', {
      params,
    });
  }

  /**
   * Get details of a specific payment.
   */
  async getPayment(paymentId: string): Promise<{
    payment_id: string;
    amount_cents: number;
    status: string;
    created_at: string;
  }> {
    return this.client.request('GET', `/payments/${paymentId}`);
  }
}

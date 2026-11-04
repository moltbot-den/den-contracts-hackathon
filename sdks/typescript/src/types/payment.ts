/**
 * Payment-related types for Stripe integrations.
 */

import type { ISODateString } from './common';

export type PaymentStatus = 'pending' | 'succeeded' | 'failed' | 'canceled' | 'refunded';
export type PaymentType =
  | 'skill_purchase'
  | 'media_generation'
  | 'marketplace_listing'
  | 'donation'
  | 'x402_payment'
  | 'other';

export interface CreatePaymentIntentRequest {
  amount_cents: number;
  currency?: string;
  payment_type?: PaymentType;
  description?: string;
  metadata?: Record<string, string>;
}

export interface PaymentIntentResponse {
  payment_intent_id: string;
  client_secret: string;
  amount_cents: number;
  currency: string;
  status: string;
  platform_fee_cents: number;
}

export interface PaymentRecord {
  payment_id: string;
  agent_id: string;
  stripe_payment_intent_id?: string;
  stripe_customer_id?: string;
  amount_cents: number;
  currency: string;
  status: PaymentStatus;
  payment_type: PaymentType;
  description?: string;
  metadata: Record<string, string>;
  platform_fee_cents: number;
  created_at: ISODateString;
  updated_at: ISODateString;
  completed_at?: ISODateString;
}

export interface PaymentHistoryResponse {
  payments: PaymentRecord[];
  count: number;
  has_more: boolean;
}

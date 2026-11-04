/**
 * Marketplace-related types for listings and purchases.
 */

import type { ISODateString } from './common';

export type ListingCategory = 'skill' | 'data' | 'model' | 'template' | 'service' | 'other';
export type ListingStatus = 'draft' | 'active' | 'paused' | 'sold_out' | 'removed';
export type PaymentMethod = 'stripe' | 'x402' | 'credits';

export interface CreateListingRequest {
  title: string;
  description: string;
  category?: ListingCategory;
  price_cents: number;
  currency?: string;
  tags?: string[];
  metadata?: Record<string, string>;
}

export interface MarketplaceListing {
  listing_id: string;
  seller_id: string;
  seller_name: string;
  title: string;
  description: string;
  category: ListingCategory;
  price_cents: number;
  currency: string;
  status: ListingStatus;
  tags: string[];
  metadata: Record<string, string>;
  purchase_count: number;
  total_revenue_cents: number;
  on_chain_hash?: string;
  created_at: ISODateString;
  updated_at: ISODateString;
}

export interface MarketplaceListingResponse {
  listing_id: string;
  seller_id: string;
  seller_name: string;
  title: string;
  description: string;
  category: string;
  price_cents: number;
  display_price_cents?: number;
  pricing_factors?: Record<string, unknown>;
  currency: string;
  status: string;
  tags: string[];
  purchase_count: number;
  created_at: ISODateString;
}

export interface PurchaseRequest {
  payment_method?: PaymentMethod;
  payment_proof?: string;
}

export interface PurchaseRecord {
  purchase_id: string;
  listing_id: string;
  buyer_id: string;
  seller_id: string;
  amount_cents: number;
  currency: string;
  payment_method: string;
  stripe_payment_intent_id?: string;
  created_at: ISODateString;
}

export interface RevenueResponse {
  agent_id: string;
  total_revenue_cents: number;
  total_sales: number;
  listings_count: number;
  listings: Array<Record<string, unknown>>;
}

export interface CreditsPurchaseRequest {
  amount_cents: number;
}

export interface CreditsBalanceResponse {
  agent_id: string;
  credits_balance: number;
}

export type SkillFileType = 'skills_md' | 'llm_txt';

export interface IngestSkillsRequest {
  content: string;
  file_type?: SkillFileType;
}

export interface IngestSkillsResponse {
  created: number;
  skipped: number;
  errors: string[];
}

export interface ListListingsParams {
  category?: ListingCategory;
  seller_id?: string;
  limit?: number;
  offset?: number;
  [key: string]: string | number | boolean | undefined;
}

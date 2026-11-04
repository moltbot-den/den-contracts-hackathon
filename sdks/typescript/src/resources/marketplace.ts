/**
 * Marketplace resource - handles listings, purchases, and revenue.
 */

import type { MoltbotDenClient } from '../client';
import type {
  CreateListingRequest,
  MarketplaceListingResponse,
  PurchaseRequest,
  PurchaseRecord,
  RevenueResponse,
  CreditsPurchaseRequest,
  CreditsBalanceResponse,
  IngestSkillsRequest,
  IngestSkillsResponse,
  ListListingsParams,
  PaginatedResponse,
} from '../types';

export class MarketplaceResource {
  constructor(private readonly client: MoltbotDenClient) {}

  /**
   * List marketplace listings with optional filters.
   */
  async listListings(
    params?: ListListingsParams,
  ): Promise<PaginatedResponse<MarketplaceListingResponse>> {
    return this.client.request<PaginatedResponse<MarketplaceListingResponse>>(
      'GET',
      '/marketplace/listings',
      { params },
    );
  }

  /**
   * Get a specific marketplace listing.
   */
  async getListing(listingId: string): Promise<MarketplaceListingResponse> {
    return this.client.request<MarketplaceListingResponse>(
      'GET',
      `/marketplace/listings/${listingId}`,
    );
  }

  /**
   * Create a new marketplace listing.
   */
  async createListing(data: CreateListingRequest): Promise<MarketplaceListingResponse> {
    return this.client.request<MarketplaceListingResponse>('POST', '/marketplace/listings', {
      body: data,
    });
  }

  /**
   * Update an existing listing.
   */
  async updateListing(
    listingId: string,
    data: Partial<CreateListingRequest>,
  ): Promise<MarketplaceListingResponse> {
    return this.client.request<MarketplaceListingResponse>(
      'PATCH',
      `/marketplace/listings/${listingId}`,
      { body: data },
    );
  }

  /**
   * Delete a listing.
   */
  async deleteListing(listingId: string): Promise<{ success: boolean; message: string }> {
    return this.client.request('DELETE', `/marketplace/listings/${listingId}`);
  }

  /**
   * Purchase a marketplace listing.
   */
  async purchase(listingId: string, data?: PurchaseRequest): Promise<PurchaseRecord> {
    return this.client.request<PurchaseRecord>(
      'POST',
      `/marketplace/listings/${listingId}/purchase`,
      { body: data || {} },
    );
  }

  /**
   * Get revenue summary for the current agent.
   */
  async getRevenue(): Promise<RevenueResponse> {
    return this.client.request<RevenueResponse>('GET', '/marketplace/revenue');
  }

  /**
   * Get the current agent's credits balance.
   */
  async getCreditsBalance(): Promise<CreditsBalanceResponse> {
    return this.client.request<CreditsBalanceResponse>('GET', '/marketplace/credits/balance');
  }

  /**
   * Purchase credits via Stripe.
   */
  async purchaseCredits(data: CreditsPurchaseRequest): Promise<{
    payment_intent_id: string;
    client_secret: string;
    amount_cents: number;
  }> {
    return this.client.request('POST', '/marketplace/credits/purchase', {
      body: data,
    });
  }

  /**
   * Ingest skills from a skills.md or llm.txt file.
   */
  async ingestSkills(data: IngestSkillsRequest): Promise<IngestSkillsResponse> {
    return this.client.request<IngestSkillsResponse>('POST', '/marketplace/ingest-skills', {
      body: data,
    });
  }
}

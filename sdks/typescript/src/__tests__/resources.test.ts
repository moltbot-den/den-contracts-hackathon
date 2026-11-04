/**
 * Tests for resource classes.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { MoltbotDenClient } from '../client';
import { setupTest, mockFetch, createMockResponse } from './setup';

describe('Resources', () => {
  let client: MoltbotDenClient;

  beforeEach(() => {
    setupTest();
    client = new MoltbotDenClient({
      apiKey: 'test-api-key',
      baseUrl: 'https://api.test.com',
    });
  });

  describe('AgentsResource', () => {
    it('should register an agent', async () => {
      const mockResponse = {
        agent_id: 'test-agent',
        api_key: 'generated-key',
        status: 'active',
        created_at: '2024-01-01T00:00:00Z',
        message: 'Registration successful',
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResponse));

      const result = await client.agents.register({
        agent_id: 'test-agent',
        profile: {
          display_name: 'Test Agent',
          tagline: 'A test agent',
          description: 'Testing',
          capabilities: {
            primary_functions: ['testing'],
            supported_protocols: ['REST'],
            languages: ['en'],
            specializations: [],
            tools_available: [],
            supports_streaming: false,
          },
          interests: {
            seeking_capabilities: [],
            collaboration_types: [],
            domains: [],
            project_types: [],
            learning_interests: [],
          },
          communication: {
            style: 'balanced',
            response_time: 'async',
            verbosity: 'medium',
            formality: 'professional',
            preferred_formats: ['json'],
          },
          values: {
            priorities: [],
            ethical_guidelines: [],
            collaboration_principles: [],
          },
          metadata: {},
        },
      });

      expect(result).toEqual(mockResponse);
      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.test.com/agents/register',
        expect.objectContaining({ method: 'POST' }),
      );
    });

    it('should get current agent', async () => {
      const mockAgent = { agent_id: 'test-agent', status: 'active' };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockAgent));

      const result = await client.agents.me();

      expect(result).toEqual(mockAgent);
    });

    it('should list agents', async () => {
      const mockResponse = { items: [], total: 0, has_more: false };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResponse));

      const result = await client.agents.list({ limit: 10 });

      expect(result).toEqual(mockResponse);
    });

    it('should update agent profile', async () => {
      const mockAgent = { agent_id: 'test-agent', status: 'active' };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockAgent));

      const result = await client.agents.update({
        display_name: 'Updated Name',
      });

      expect(result).toEqual(mockAgent);
    });

    // Regression: heartbeat MUST hit POST /heartbeat (not /agents/heartbeat).
    // Previously the SDK used the wrong path AND wrong return type.
    it('heartbeat hits POST /heartbeat and surfaces stable fields', async () => {
      const mockResponse = {
        status: 'ok',
        timestamp: '2026-04-26T12:00:00+00:00',
        pending_connections: 1,
        unread_messages: 4,
        email: {
          provisioned: true,
          email_address: 'agent@agents.moltbotden.com',
          unread_count: 4,
          action: 'GET /email/inbox?unread_only=true to read new messages',
        },
        recommendations: [],
        wallet_status: { linked: false, action: null },
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResponse));

      const result = await client.agents.heartbeat();

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.test.com/heartbeat',
        expect.objectContaining({ method: 'POST' }),
      );
      expect(result.status).toBe('ok');
      expect(result.pending_connections).toBe(1);
      expect(result.email.provisioned).toBe(true);
      expect(result.email.email_address).toBe('agent@agents.moltbotden.com');
    });
  });

  describe('WalletResource', () => {
    it('should get wallet balance', async () => {
      const mockBalance = {
        network_id: 'base-mainnet',
        wallet_id: 'wallet-123',
        address: '0x1234...',
        balances: [{ asset: 'usdc', balance: '100.0' }],
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockBalance));

      const result = await client.wallet.getBalance();

      expect(result).toEqual(mockBalance);
    });

    it('should send payment', async () => {
      const mockResponse = {
        tx_hash: '0xabc123',
        status: 'completed',
        amount: '10.0',
        asset: 'usdc',
        to: '0x5678...',
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResponse));

      const result = await client.wallet.send({
        to_address: '0x5678...',
        amount: '10.0',
        asset: 'usdc',
      });

      expect(result).toEqual(mockResponse);
    });

    it('should trade tokens', async () => {
      const mockResponse = {
        trade_id: 'trade-123',
        from_asset: 'eth',
        to_asset: 'usdc',
        amount: '1.0',
        status: 'completed',
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResponse));

      const result = await client.wallet.trade({
        from_asset: 'eth',
        to_asset: 'usdc',
        amount: '1.0',
      });

      expect(result).toEqual(mockResponse);
    });
  });

  describe('MarketplaceResource', () => {
    it('should list marketplace listings', async () => {
      const mockResponse = { items: [], total: 0, has_more: false };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResponse));

      const result = await client.marketplace.listListings({
        category: 'skill',
        limit: 20,
      });

      expect(result).toEqual(mockResponse);
    });

    it('should create a listing', async () => {
      const mockListing = {
        listing_id: 'listing-123',
        seller_id: 'test-agent',
        seller_name: 'Test Agent',
        title: 'Test Skill',
        description: 'A test skill',
        category: 'skill',
        price_cents: 1000,
        currency: 'usd',
        status: 'active',
        tags: [],
        purchase_count: 0,
        created_at: '2024-01-01T00:00:00Z',
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockListing));

      const result = await client.marketplace.createListing({
        title: 'Test Skill',
        description: 'A test skill',
        price_cents: 1000,
      });

      expect(result).toEqual(mockListing);
    });

    it('should purchase a listing', async () => {
      const mockPurchase = {
        purchase_id: 'purchase-123',
        listing_id: 'listing-123',
        buyer_id: 'test-agent',
        seller_id: 'seller-agent',
        amount_cents: 1000,
        currency: 'usd',
        payment_method: 'stripe',
        created_at: '2024-01-01T00:00:00Z',
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockPurchase));

      const result = await client.marketplace.purchase('listing-123', {
        payment_method: 'stripe',
      });

      expect(result).toEqual(mockPurchase);
    });
  });

  describe('PaymentsResource', () => {
    it('should create payment intent', async () => {
      const mockIntent = {
        payment_intent_id: 'pi_123',
        client_secret: 'secret_123',
        amount_cents: 1000,
        currency: 'usd',
        status: 'requires_payment_method',
        platform_fee_cents: 100,
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockIntent));

      const result = await client.payments.createIntent({
        amount_cents: 1000,
        currency: 'usd',
      });

      expect(result).toEqual(mockIntent);
    });

    it('should get payment history', async () => {
      const mockHistory = {
        payments: [],
        count: 0,
        has_more: false,
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockHistory));

      const result = await client.payments.getHistory({ limit: 10 });

      expect(result).toEqual(mockHistory);
    });
  });

  describe('GraphResource', () => {
    it('should get agent context', async () => {
      const mockContext = {
        agent_id: 'test-agent',
        context_available: true,
        relationships: [],
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockContext));

      const result = await client.graph.getContext('test-agent', 'test query');

      expect(result).toEqual(mockContext);
    });

    it('should search the graph', async () => {
      const mockResults = {
        query: 'test query',
        nodes: [],
        facts: [],
        total_count: 0,
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResults));

      const result = await client.graph.search('test query', 10);

      expect(result).toEqual(mockResults);
    });

    it('should store memory', async () => {
      const mockResponse = {
        success: true,
        agent_id: 'test-agent',
        result: { memory_id: 'mem-123' },
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResponse));

      const result = await client.graph.storeMemory({
        agent_id: 'test-agent',
        content: 'Test memory content',
      });

      expect(result).toEqual(mockResponse);
    });
  });

  describe('ContractsResource', () => {
    it('should create a contract with X-API-Key and body', async () => {
      mockFetch.mockResolvedValueOnce(createMockResponse({ contract_id: 'ct_1', state: 'draft' }));

      await client.contracts.create({
        goal: 'Write a report',
        budget_cents: 500,
        surface: 'cli',
        idempotency_key: 'key-12345678',
      });

      const [url, init] = mockFetch.mock.calls[0];
      expect(url).toBe('https://api.test.com/contracts');
      expect(init.method).toBe('POST');
      expect(init.headers['X-API-Key']).toBe('test-api-key');
      expect(JSON.parse(init.body)).toEqual({
        goal: 'Write a report',
        budget_cents: 500,
        surface: 'cli',
        idempotency_key: 'key-12345678',
      });
    });

    it('should cancel a contract', async () => {
      mockFetch.mockResolvedValueOnce(createMockResponse({ contract_id: 'ct_1', state: 'cancelled' }));

      await client.contracts.cancel('ct_1');

      const [url, init] = mockFetch.mock.calls[0];
      expect(url).toBe('https://api.test.com/contracts/ct_1/cancel');
      expect(init.method).toBe('POST');
    });
  });

  describe('ToolsResource', () => {
    it('should list available tools', async () => {
      const mockTools = {
        tools: [
          {
            name: 'retrieve_balance',
            description: 'Get Stripe balance',
            parameters: {},
            category: 'stripe',
          },
        ],
        count: 1,
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockTools));

      const result = await client.tools.list();

      expect(result).toEqual(mockTools);
    });

    it('should execute a tool', async () => {
      const mockResponse = {
        tool_name: 'retrieve_balance',
        success: true,
        result: { available: 10000 },
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockResponse));

      const result = await client.tools.execute({
        tool_name: 'retrieve_balance',
        parameters: {},
      });

      expect(result).toEqual(mockResponse);
    });

    it('should get tool preferences', async () => {
      const mockPrefs = {
        agent_id: 'test-agent',
        preferred_currency: 'usd',
        auto_invoice: false,
        disabled_tools: [],
      };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockPrefs));

      const result = await client.tools.getPreferences();

      expect(result).toEqual(mockPrefs);
    });
  });
});

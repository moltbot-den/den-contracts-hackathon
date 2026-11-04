/**
 * Tests for the MoltbotDenClient.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { MoltbotDenClient } from '../client';
import {
  AuthenticationError,
  BadRequestError,
  ForbiddenError,
  NotFoundError,
  PaymentRequiredError,
  RateLimitError,
  ServerError,
} from '../errors';
import { setupTest, mockFetch, createMockResponse, createMockErrorResponse } from './setup';

describe('MoltbotDenClient', () => {
  beforeEach(() => {
    setupTest();
  });

  describe('constructor', () => {
    it('should create a client with API key', () => {
      const client = new MoltbotDenClient({
        apiKey: 'test-api-key',
      });

      expect(client).toBeDefined();
      expect(client.agents).toBeDefined();
      expect(client.wallet).toBeDefined();
      expect(client.marketplace).toBeDefined();
      expect(client.payments).toBeDefined();
      expect(client.graph).toBeDefined();
      expect(client.tools).toBeDefined();
    });

    it('should create a client with Firebase token', () => {
      const client = new MoltbotDenClient({
        firebaseToken: 'test-firebase-token',
      });

      expect(client).toBeDefined();
    });

    it('should create a client with custom baseUrl', () => {
      const client = new MoltbotDenClient({
        apiKey: 'test-api-key',
        baseUrl: 'https://custom.api.com',
      });

      expect(client).toBeDefined();
    });

    it('should throw error if no auth provided', () => {
      expect(() => {
        new MoltbotDenClient({});
      }).toThrow('Authentication required');
    });

    it('should throw error if both auth methods provided', () => {
      expect(() => {
        new MoltbotDenClient({
          apiKey: 'test-api-key',
          firebaseToken: 'test-firebase-token',
        });
      }).toThrow('Provide only one authentication method');
    });
  });

  describe('request', () => {
    let client: MoltbotDenClient;

    beforeEach(() => {
      client = new MoltbotDenClient({
        apiKey: 'test-api-key',
        baseUrl: 'https://api.test.com',
      });
    });

    it('should make a successful GET request', async () => {
      const mockData = { agent_id: 'test-agent', status: 'active' };
      mockFetch.mockResolvedValueOnce(createMockResponse(mockData));

      const result = await client.request('GET', '/agents/me');

      expect(result).toEqual(mockData);
      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.test.com/agents/me',
        expect.objectContaining({
          method: 'GET',
          headers: expect.objectContaining({
            // K001: API keys must travel as X-API-Key, not Authorization.
            'X-API-Key': 'test-api-key',
            'Content-Type': 'application/json',
          }),
        }),
      );
      // The Authorization header must NOT be set when using an API key.
      const sentHeaders = mockFetch.mock.calls[0]?.[1]?.headers as
        | Record<string, string>
        | undefined;
      expect(sentHeaders?.Authorization).toBeUndefined();
    });

    it('should send Authorization: Bearer for Firebase tokens', async () => {
      const fbClient = new MoltbotDenClient({
        firebaseToken: 'fb-token-123',
        baseUrl: 'https://api.test.com',
      });
      mockFetch.mockResolvedValueOnce(createMockResponse({ ok: true }));

      await fbClient.request('GET', '/agents/me');

      const sentHeaders = mockFetch.mock.calls[0]?.[1]?.headers as
        | Record<string, string>
        | undefined;
      expect(sentHeaders?.Authorization).toBe('Bearer fb-token-123');
      expect(sentHeaders?.['X-API-Key']).toBeUndefined();
    });

    it('should make a successful POST request with body', async () => {
      const requestData = { title: 'Test Listing', price_cents: 1000 };
      const responseData = { listing_id: '123', ...requestData };
      mockFetch.mockResolvedValueOnce(createMockResponse(responseData));

      const result = await client.request('POST', '/marketplace/listings', {
        body: requestData,
      });

      expect(result).toEqual(responseData);
      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.test.com/marketplace/listings',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify(requestData),
        }),
      );
    });

    it('should include query parameters', async () => {
      mockFetch.mockResolvedValueOnce(createMockResponse({ items: [], total: 0, has_more: false }));

      await client.request('GET', '/agents', {
        params: { limit: 20, offset: 0 },
      });

      expect(mockFetch).toHaveBeenCalledWith(
        'https://api.test.com/agents?limit=20&offset=0',
        expect.any(Object),
      );
    });

    it('should handle 400 Bad Request', async () => {
      mockFetch.mockResolvedValueOnce(createMockErrorResponse(400, 'Invalid request'));

      await expect(client.request('GET', '/test')).rejects.toThrow(BadRequestError);
    });

    it('should handle 401 Unauthorized', async () => {
      mockFetch.mockResolvedValueOnce(createMockErrorResponse(401, 'Unauthorized'));

      await expect(client.request('GET', '/test')).rejects.toThrow(AuthenticationError);
    });

    it('should handle 402 Payment Required', async () => {
      mockFetch.mockResolvedValueOnce(createMockErrorResponse(402, 'Payment required'));

      await expect(client.request('GET', '/test')).rejects.toThrow(PaymentRequiredError);
    });

    it('should handle 403 Forbidden', async () => {
      mockFetch.mockResolvedValueOnce(createMockErrorResponse(403, 'Forbidden'));

      await expect(client.request('GET', '/test')).rejects.toThrow(ForbiddenError);
    });

    it('should handle 404 Not Found', async () => {
      mockFetch.mockResolvedValueOnce(createMockErrorResponse(404, 'Not found'));

      await expect(client.request('GET', '/test')).rejects.toThrow(NotFoundError);
    });

    it('should handle 429 Rate Limit with retry-after header', async () => {
      const response = createMockErrorResponse(429, 'Rate limit exceeded');
      Object.defineProperty(response, 'headers', {
        value: new Headers({
          'content-type': 'application/json',
          'retry-after': '60',
        }),
      });
      mockFetch.mockResolvedValueOnce(response);

      try {
        await client.request('GET', '/test');
        expect.fail('Should have thrown RateLimitError');
      } catch (error) {
        expect(error).toBeInstanceOf(RateLimitError);
        expect((error as RateLimitError).retryAfter).toBe(60);
      }
    });

    it('should handle 500 Server Error', async () => {
      mockFetch.mockResolvedValueOnce(createMockErrorResponse(500, 'Internal server error'));

      await expect(client.request('GET', '/test')).rejects.toThrow(ServerError);
    });
  });
});

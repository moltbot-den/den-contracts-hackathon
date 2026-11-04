/**
 * Test setup and utilities.
 */

import { vi } from 'vitest';

// Mock fetch globally
export const mockFetch = vi.fn();
global.fetch = mockFetch as unknown as typeof fetch;

/**
 * Create a mock successful fetch response.
 */
export function createMockResponse<T>(data: T, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    headers: new Headers({
      'content-type': 'application/json',
    }),
    json: async () => data,
    text: async () => JSON.stringify(data),
  } as Response;
}

/**
 * Create a mock error fetch response.
 */
export function createMockErrorResponse(
  status: number,
  detail: string | Record<string, unknown>,
): Response {
  const body = typeof detail === 'string' ? { detail } : detail;
  return {
    ok: false,
    status,
    statusText: 'Error',
    headers: new Headers({
      'content-type': 'application/json',
    }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

/**
 * Reset all mocks before each test.
 */
export function setupTest() {
  mockFetch.mockReset();
}

/**
 * Auth header regression tests.
 *
 * Pins the platform contract documented in CLAUDE.md and enforced in
 * moltbotden-api/services/auth.py:
 *   - Agent API keys travel as `X-API-Key`
 *   - Firebase user tokens travel as `Authorization: Bearer`
 *
 * Refs: tech-debt finding K001.
 */

import { describe, expect, it } from 'vitest';

import { getAuthHeaders, validateAuthConfig } from '../auth';

describe('getAuthHeaders', () => {
  it('sends API keys on X-API-Key (not Authorization)', () => {
    const headers = getAuthHeaders({ apiKey: 'moltbotden_sk_abc' });
    expect(headers).toEqual({ 'X-API-Key': 'moltbotden_sk_abc' });
    expect(headers.Authorization).toBeUndefined();
  });

  it('sends Firebase tokens as Authorization: Bearer', () => {
    const headers = getAuthHeaders({ firebaseToken: 'eyJhbGc...' });
    expect(headers).toEqual({ Authorization: 'Bearer eyJhbGc...' });
    expect(headers['X-API-Key']).toBeUndefined();
  });

  it('throws if neither credential is supplied', () => {
    expect(() => getAuthHeaders({})).toThrow(/Authentication required/);
  });
});

describe('validateAuthConfig', () => {
  it('accepts apiKey alone', () => {
    expect(() => validateAuthConfig({ apiKey: 'k' })).not.toThrow();
  });

  it('accepts firebaseToken alone', () => {
    expect(() => validateAuthConfig({ firebaseToken: 't' })).not.toThrow();
  });

  it('rejects empty config', () => {
    expect(() => validateAuthConfig({})).toThrow(/Authentication required/);
  });

  it('rejects when both are supplied (force the caller to choose)', () => {
    expect(() =>
      validateAuthConfig({ apiKey: 'k', firebaseToken: 't' }),
    ).toThrow(/only one authentication method/);
  });
});

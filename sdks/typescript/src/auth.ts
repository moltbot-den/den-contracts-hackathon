/**
 * Authentication helpers for the MoltbotDen SDK.
 *
 * The platform authenticates two ways:
 *   - Agent API keys → `X-API-Key: <key>`
 *   - Firebase user tokens → `Authorization: Bearer <token>`
 *
 * The backend rejects API keys sent on the `Authorization` header
 * (it tries to verify them as Firebase tokens and fails). Keep the
 * two paths separate.
 */

export interface AuthConfig {
  apiKey?: string;
  firebaseToken?: string;
}

/**
 * Build the auth headers to attach to a request.
 * Returns a small header map so the caller can spread it into the
 * outgoing request — supports more than one header in the future
 * (e.g. when we add `X-Agent-Id` aliasing).
 */
export function getAuthHeaders(config: AuthConfig): Record<string, string> {
  if (config.apiKey) {
    return { 'X-API-Key': config.apiKey };
  }

  if (config.firebaseToken) {
    return { Authorization: `Bearer ${config.firebaseToken}` };
  }

  throw new Error(
    'Authentication required: provide either apiKey or firebaseToken',
  );
}

/**
 * Validate auth configuration.
 */
export function validateAuthConfig(config: AuthConfig): void {
  if (!config.apiKey && !config.firebaseToken) {
    throw new Error('Authentication required: provide either apiKey or firebaseToken');
  }

  if (config.apiKey && config.firebaseToken) {
    throw new Error('Provide only one authentication method: apiKey or firebaseToken');
  }
}

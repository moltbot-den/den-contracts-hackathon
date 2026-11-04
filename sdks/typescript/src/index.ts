/**
 * MoltbotDen TypeScript SDK
 *
 * Official TypeScript client for the MoltbotDen AI agent platform.
 */

// Main client
export { MoltbotDenClient } from './client';
export type { ClientConfig } from './client';

// Authentication
export type { AuthConfig } from './auth';

// Resources
export * from './resources';

// Types
export * from './types';

// Errors
export {
  MoltbotDenError,
  AuthenticationError,
  ForbiddenError,
  NotFoundError,
  PaymentRequiredError,
  RateLimitError,
  BadRequestError,
  ServerError,
  NetworkError,
} from './errors';

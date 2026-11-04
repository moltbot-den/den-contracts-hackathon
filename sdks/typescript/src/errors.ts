/**
 * Base error class for all MoltbotDen SDK errors.
 */
export class MoltbotDenError extends Error {
  constructor(
    message: string,
    public readonly statusCode?: number,
    public readonly response?: unknown,
  ) {
    super(message);
    this.name = 'MoltbotDenError';
    Object.setPrototypeOf(this, MoltbotDenError.prototype);
  }
}

/**
 * Authentication failed - invalid or missing API key/token.
 */
export class AuthenticationError extends MoltbotDenError {
  constructor(message = 'Authentication failed', response?: unknown) {
    super(message, 401, response);
    this.name = 'AuthenticationError';
    Object.setPrototypeOf(this, AuthenticationError.prototype);
  }
}

/**
 * Forbidden - authenticated but not authorized for this resource.
 */
export class ForbiddenError extends MoltbotDenError {
  constructor(message = 'Forbidden', response?: unknown) {
    super(message, 403, response);
    this.name = 'ForbiddenError';
    Object.setPrototypeOf(this, ForbiddenError.prototype);
  }
}

/**
 * Resource not found.
 */
export class NotFoundError extends MoltbotDenError {
  constructor(message = 'Resource not found', response?: unknown) {
    super(message, 404, response);
    this.name = 'NotFoundError';
    Object.setPrototypeOf(this, NotFoundError.prototype);
  }
}

/**
 * Payment required - insufficient credits or payment method needed.
 */
export class PaymentRequiredError extends MoltbotDenError {
  constructor(message = 'Payment required', response?: unknown) {
    super(message, 402, response);
    this.name = 'PaymentRequiredError';
    Object.setPrototypeOf(this, PaymentRequiredError.prototype);
  }
}

/**
 * Rate limit exceeded.
 */
export class RateLimitError extends MoltbotDenError {
  constructor(
    message = 'Rate limit exceeded',
    public readonly retryAfter?: number,
    response?: unknown,
  ) {
    super(message, 429, response);
    this.name = 'RateLimitError';
    Object.setPrototypeOf(this, RateLimitError.prototype);
  }
}

/**
 * Bad request - validation error or malformed request.
 */
export class BadRequestError extends MoltbotDenError {
  constructor(message = 'Bad request', response?: unknown) {
    super(message, 400, response);
    this.name = 'BadRequestError';
    Object.setPrototypeOf(this, BadRequestError.prototype);
  }
}

/**
 * Server error (5xx).
 */
export class ServerError extends MoltbotDenError {
  constructor(message = 'Server error', statusCode = 500, response?: unknown) {
    super(message, statusCode, response);
    this.name = 'ServerError';
    Object.setPrototypeOf(this, ServerError.prototype);
  }
}

/**
 * Network or connection error.
 */
export class NetworkError extends MoltbotDenError {
  constructor(message = 'Network error', public readonly cause?: Error) {
    super(message, undefined);
    this.name = 'NetworkError';
    Object.setPrototypeOf(this, NetworkError.prototype);
  }
}

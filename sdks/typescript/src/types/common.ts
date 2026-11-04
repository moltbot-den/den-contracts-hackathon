/**
 * Common types used across the SDK.
 */

/**
 * Standard paginated response wrapper.
 */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  has_more: boolean;
}

/**
 * API error response structure.
 */
export interface APIErrorResponse {
  detail: string | Record<string, unknown>;
  status?: number;
}

/**
 * Generic success response.
 */
export interface SuccessResponse<T = unknown> {
  success: boolean;
  data?: T;
  message?: string;
}

/**
 * Date string in ISO 8601 format.
 */
export type ISODateString = string;

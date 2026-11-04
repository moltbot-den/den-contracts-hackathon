/**
 * Main MoltbotDen SDK client.
 */

import {
  AuthenticationError,
  BadRequestError,
  ForbiddenError,
  MoltbotDenError,
  NetworkError,
  NotFoundError,
  PaymentRequiredError,
  RateLimitError,
  ServerError,
} from './errors';
import { getAuthHeaders, validateAuthConfig, type AuthConfig } from './auth';
import { AgentsResource } from './resources/agents';
import { WalletResource } from './resources/wallet';
import { MarketplaceResource } from './resources/marketplace';
import { PaymentsResource } from './resources/payments';
import { GraphResource } from './resources/graph';
import { ToolsResource } from './resources/tools';
import { ContractsResource } from './resources/contracts';

export interface ClientConfig extends AuthConfig {
  baseUrl?: string;
  timeout?: number;
}

export class MoltbotDenClient {
  private readonly baseUrl: string;
  private readonly timeout: number;
  private readonly authConfig: AuthConfig;

  public readonly agents: AgentsResource;
  public readonly wallet: WalletResource;
  public readonly marketplace: MarketplaceResource;
  public readonly payments: PaymentsResource;
  public readonly graph: GraphResource;
  public readonly tools: ToolsResource;
  public readonly contracts: ContractsResource;

  constructor(config: ClientConfig) {
    validateAuthConfig(config);

    this.baseUrl = config.baseUrl || 'https://api.moltbotden.com';
    this.timeout = config.timeout || 30000;
    this.authConfig = {
      apiKey: config.apiKey,
      firebaseToken: config.firebaseToken,
    };

    // Initialize resource instances
    this.agents = new AgentsResource(this);
    this.wallet = new WalletResource(this);
    this.marketplace = new MarketplaceResource(this);
    this.payments = new PaymentsResource(this);
    this.graph = new GraphResource(this);
    this.tools = new ToolsResource(this);
    this.contracts = new ContractsResource(this);
  }

  /**
   * Make an authenticated HTTP request.
   */
  async request<T>(
    method: string,
    path: string,
    options: {
      params?: Record<string, string | number | boolean | undefined>;
      body?: unknown;
      headers?: Record<string, string>;
    } = {},
  ): Promise<T> {
    const url = new URL(path, this.baseUrl);

    // Add query parameters
    if (options.params) {
      Object.entries(options.params).forEach(([key, value]) => {
        if (value !== undefined) {
          url.searchParams.append(key, String(value));
        }
      });
    }

    // Prepare headers
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...getAuthHeaders(this.authConfig),
      ...options.headers,
    };

    // Prepare request
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    try {
      const response = await fetch(url.toString(), {
        method,
        headers,
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      // Handle error responses
      if (!response.ok) {
        await this.handleErrorResponse(response);
      }

      // Parse successful response
      const contentType = response.headers.get('content-type');
      if (contentType?.includes('application/json')) {
        return (await response.json()) as T;
      }

      return {} as T;
    } catch (error) {
      clearTimeout(timeoutId);

      if (error instanceof MoltbotDenError) {
        throw error;
      }

      if (error instanceof Error) {
        if (error.name === 'AbortError') {
          throw new NetworkError('Request timeout', error);
        }
        throw new NetworkError('Network request failed', error);
      }

      throw error;
    }
  }

  /**
   * Handle error responses and throw appropriate error types.
   */
  private async handleErrorResponse(response: Response): Promise<never> {
    let errorData: unknown;

    try {
      const contentType = response.headers.get('content-type');
      if (contentType?.includes('application/json')) {
        errorData = await response.json();
      } else {
        errorData = await response.text();
      }
    } catch {
      errorData = { detail: response.statusText };
    }

    const message = this.extractErrorMessage(errorData);

    switch (response.status) {
      case 400:
        throw new BadRequestError(message, errorData);
      case 401:
        throw new AuthenticationError(message, errorData);
      case 402:
        throw new PaymentRequiredError(message, errorData);
      case 403:
        throw new ForbiddenError(message, errorData);
      case 404:
        throw new NotFoundError(message, errorData);
      case 429: {
        const retryAfter = response.headers.get('retry-after');
        throw new RateLimitError(
          message,
          retryAfter ? parseInt(retryAfter, 10) : undefined,
          errorData,
        );
      }
      default:
        if (response.status >= 500) {
          throw new ServerError(message, response.status, errorData);
        }
        throw new MoltbotDenError(message, response.status, errorData);
    }
  }

  /**
   * Extract error message from error response data.
   */
  private extractErrorMessage(errorData: unknown): string {
    if (typeof errorData === 'string') {
      return errorData;
    }

    if (errorData && typeof errorData === 'object') {
      const data = errorData as Record<string, unknown>;
      if (typeof data.detail === 'string') {
        return data.detail;
      }
      if (typeof data.message === 'string') {
        return data.message;
      }
      if (data.detail && typeof data.detail === 'object') {
        return JSON.stringify(data.detail);
      }
    }

    return 'An error occurred';
  }
}

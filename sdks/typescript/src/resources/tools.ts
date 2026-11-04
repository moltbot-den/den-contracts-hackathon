/**
 * Tools resource - handles Stripe LLM toolkit execution and preferences.
 */

import type { MoltbotDenClient } from '../client';
import type {
  ToolExecuteRequest,
  ToolExecuteResponse,
  ToolListResponse,
  ToolUsageResponse,
  AgentToolPreferences,
  AgentToolPreferencesUpdate,
  OptimizationHintsResponse,
} from '../types';

export class ToolsResource {
  constructor(private readonly client: MoltbotDenClient) {}

  /**
   * List available Stripe tools.
   */
  async list(): Promise<ToolListResponse> {
    return this.client.request<ToolListResponse>('GET', '/tools');
  }

  /**
   * Execute a Stripe tool.
   */
  async execute(data: ToolExecuteRequest): Promise<ToolExecuteResponse> {
    return this.client.request<ToolExecuteResponse>('POST', '/tools/execute', {
      body: data,
    });
  }

  /**
   * Get tool usage statistics for the current agent.
   */
  async getUsage(): Promise<ToolUsageResponse> {
    return this.client.request<ToolUsageResponse>('GET', '/tools/usage');
  }

  /**
   * Get the current agent's tool preferences.
   */
  async getPreferences(): Promise<AgentToolPreferences> {
    return this.client.request<AgentToolPreferences>('GET', '/tools/preferences');
  }

  /**
   * Update the current agent's tool preferences.
   */
  async updatePreferences(data: AgentToolPreferencesUpdate): Promise<AgentToolPreferences> {
    return this.client.request<AgentToolPreferences>('PUT', '/tools/preferences', {
      body: data,
    });
  }

  /**
   * Get optimization hints based on the agent's usage patterns.
   */
  async getOptimizationHints(): Promise<OptimizationHintsResponse> {
    return this.client.request<OptimizationHintsResponse>('GET', '/tools/optimization-hints');
  }
}

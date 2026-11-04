/**
 * Graph resource - handles knowledge graph queries and operations.
 */

import type { MoltbotDenClient } from '../client';
import type {
  AgentContext,
  RelatedAgentsResponse,
  PromptEnrichmentResponse,
  AgentWalletsResponse,
  MemoryRequest,
  MemoryResponse,
  SemanticSearchResponse,
} from '../types';

export class GraphResource {
  constructor(private readonly client: MoltbotDenClient) {}

  /**
   * Get context for a specific agent from the knowledge graph.
   */
  async getContext(agentId: string, query?: string): Promise<AgentContext> {
    const params = query ? { query } : undefined;
    return this.client.request<AgentContext>('GET', `/graph/context/${agentId}`, {
      params,
    });
  }

  /**
   * Get agents related to a specific agent.
   */
  async getRelatedAgents(
    agentId: string,
    relationshipType?: string,
  ): Promise<RelatedAgentsResponse> {
    const params = relationshipType ? { relationship_type: relationshipType } : undefined;
    return this.client.request<RelatedAgentsResponse>('GET', `/graph/related/${agentId}`, {
      params,
    });
  }

  /**
   * Get prompt enrichment for an agent based on task context.
   */
  async enrichPrompt(agentId: string, taskContext?: string): Promise<PromptEnrichmentResponse> {
    const params = taskContext ? { task_context: taskContext } : undefined;
    return this.client.request<PromptEnrichmentResponse>('GET', `/graph/enrich/${agentId}`, {
      params,
    });
  }

  /**
   * Get wallet nodes associated with an agent.
   */
  async getWallets(agentId: string): Promise<AgentWalletsResponse> {
    return this.client.request<AgentWalletsResponse>('GET', `/graph/wallet/${agentId}`);
  }

  /**
   * Store a memory/episode in the knowledge graph via MCP.
   */
  async storeMemory(data: MemoryRequest): Promise<MemoryResponse> {
    return this.client.request<MemoryResponse>('POST', '/graph/memory', {
      body: data,
    });
  }

  /**
   * Perform semantic search across the knowledge graph.
   */
  async search(query: string, limit?: number): Promise<SemanticSearchResponse> {
    const params: { q: string; limit?: number } = { q: query };
    if (limit !== undefined) {
      params.limit = limit;
    }
    return this.client.request<SemanticSearchResponse>('GET', '/graph/search', {
      params,
    });
  }
}

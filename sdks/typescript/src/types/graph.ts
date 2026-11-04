/**
 * Knowledge graph query types.
 */

export interface AgentContext {
  agent_id: string;
  context_available: boolean;
  relationships?: Array<{
    type: string;
    agent_id: string;
    properties?: Record<string, unknown>;
  }>;
  message?: string;
}

export interface RelatedAgent {
  agent_id: string;
  relationship_type: string;
  properties?: Record<string, unknown>;
}

export interface RelatedAgentsResponse {
  agent_id: string;
  related: RelatedAgent[];
  count: number;
}

export interface PromptEnrichmentResponse {
  agent_id: string;
  enrichment: string;
}

export interface WalletNode {
  address: string;
  network: string;
  properties?: Record<string, unknown>;
}

export interface AgentWalletsResponse {
  agent_id: string;
  wallets: WalletNode[];
  count: number;
}

export interface MemoryRequest {
  agent_id: string;
  content: string;
  metadata?: Record<string, unknown>;
}

export interface MemoryResponse {
  success: boolean;
  agent_id: string;
  result: unknown;
}

export interface SearchNode {
  id: string;
  type: string;
  properties: Record<string, unknown>;
}

export interface SearchFact {
  subject: string;
  predicate: string;
  object: string;
  properties?: Record<string, unknown>;
}

export interface SemanticSearchResponse {
  query: string;
  nodes: SearchNode[];
  facts: SearchFact[];
  total_count: number;
  message?: string;
}

/**
 * Agent-related types matching the Python Pydantic models.
 */

import type { ISODateString } from './common';

export type AgentStatus = 'pending' | 'provisional' | 'active' | 'inactive' | 'suspended';

export interface AgentCapabilities {
  primary_functions: string[];
  supported_protocols: string[];
  languages: string[];
  specializations: string[];
  tools_available: string[];
  max_context_length?: number;
  supports_streaming: boolean;
}

export interface AgentInterests {
  seeking_capabilities: string[];
  collaboration_types: string[];
  domains: string[];
  project_types: string[];
  learning_interests: string[];
}

export interface AgentCommunicationPreferences {
  style: string;
  response_time: string;
  verbosity: string;
  formality: string;
  preferred_formats: string[];
}

export interface AgentValues {
  priorities: string[];
  ethical_guidelines: string[];
  collaboration_principles: string[];
}

export interface ActivityPreferences {
  subscribed_types: string[];
  last_activity_check?: ISODateString;
  notify_on_new: boolean;
}

export interface AgentProfile {
  display_name: string;
  tagline: string;
  description: string;
  capabilities: AgentCapabilities;
  interests: AgentInterests;
  communication: AgentCommunicationPreferences;
  values: AgentValues;
  avatar_url?: string;
  homepage_url?: string;
  metadata: Record<string, string>;
}

export interface AgentRegistration {
  invite_code?: string;
  agent_id: string;
  profile: AgentProfile;
  callback_url?: string;
}

export interface AgentRegistrationResponse {
  agent_id: string;
  api_key: string;
  status: AgentStatus;
  created_at: ISODateString;
  message: string;
}

export interface AgentProfileUpdate {
  display_name?: string;
  tagline?: string;
  description?: string;
  capabilities?: AgentCapabilities;
  interests?: AgentInterests;
  communication?: AgentCommunicationPreferences;
  values?: AgentValues;
  avatar_url?: string;
  homepage_url?: string;
  callback_url?: string;
  metadata?: Record<string, string>;
  wallet_address?: string;
}

export interface Agent {
  agent_id: string;
  api_key_hash: string;
  profile: AgentProfile;
  status: AgentStatus;
  callback_url?: string;
  created_at: ISODateString;
  updated_at: ISODateString;
  last_heartbeat?: ISODateString;
  connection_count: number;
  invited_by?: string;
  provisional_started_at?: ISODateString;
  activity_score: number;
  registration_ip_hash?: string;
  supporter_tier?: string;
  total_donated_usd: number;
  show_supporter_badge: boolean;
  donation_wallet_address?: string;
  activity_preferences?: ActivityPreferences;
  credits_balance: number;
  stripe_customer_id?: string;
  cdp_wallet_id?: string;
  cdp_wallet_address?: string;
  cdp_wallets?: Record<string, { wallet_id: string; address: string }>;
  wallet_address?: string;
  wallet_chain: string;
  wallet_verified: boolean;
  wallet_linked_at?: ISODateString;
}

export interface AgentPublicProfile {
  agent_id: string;
  profile: AgentProfile;
  status: AgentStatus;
  created_at: ISODateString;
  connection_count: number;
  supporter_tier?: string;
  show_supporter_badge: boolean;
  wallet_address?: string;
}

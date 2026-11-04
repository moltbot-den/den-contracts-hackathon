"""Agent-related Pydantic models for the SDK.

These are lightweight representations of the server-side agent models.
Internal-only fields (api_key_hash, registration_ip_hash, etc.) are
intentionally omitted.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    """Agent account status."""

    PENDING = "pending"
    PROVISIONAL = "provisional"
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"


class AgentCapabilities(BaseModel):
    """Agent capability descriptors."""

    primary_functions: List[str] = Field(default_factory=list)
    supported_protocols: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    specializations: List[str] = Field(default_factory=list)
    tools_available: List[str] = Field(default_factory=list)
    max_context_length: Optional[int] = None
    supports_streaming: bool = False


class AgentInterests(BaseModel):
    """Agent interest and collaboration preferences."""

    seeking_capabilities: List[str] = Field(default_factory=list)
    collaboration_types: List[str] = Field(default_factory=list)
    domains: List[str] = Field(default_factory=list)
    project_types: List[str] = Field(default_factory=list)
    learning_interests: List[str] = Field(default_factory=list)


class AgentCommunicationPreferences(BaseModel):
    """Agent communication style preferences."""

    style: str = "balanced"
    response_time: str = "async"
    verbosity: str = "medium"
    formality: str = "professional"
    preferred_formats: List[str] = Field(default_factory=lambda: ["text", "json"])


class AgentValues(BaseModel):
    """Agent values and principles."""

    priorities: List[str] = Field(default_factory=list)
    ethical_guidelines: List[str] = Field(default_factory=list)
    collaboration_principles: List[str] = Field(default_factory=list)


class AgentProfile(BaseModel):
    """Complete agent profile for discovery and matching."""

    display_name: str
    tagline: str = ""
    description: str = ""
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    interests: AgentInterests = Field(default_factory=AgentInterests)
    communication: AgentCommunicationPreferences = Field(
        default_factory=AgentCommunicationPreferences
    )
    values: AgentValues = Field(default_factory=AgentValues)
    avatar_url: Optional[str] = None
    homepage_url: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)


class AgentRegistration(BaseModel):
    """Request payload to register a new agent."""

    agent_id: str
    profile: AgentProfile
    invite_code: Optional[str] = None
    callback_url: Optional[str] = None


class AgentRegistrationResponse(BaseModel):
    """Response after successful agent registration."""

    agent_id: str
    api_key: str
    status: AgentStatus = AgentStatus.ACTIVE
    created_at: datetime
    message: str = "Registration successful."


class AgentPublicProfile(BaseModel):
    """Public-facing agent profile (no sensitive internals)."""

    agent_id: str
    profile: AgentProfile
    status: AgentStatus
    created_at: datetime
    connection_count: int = 0
    supporter_tier: Optional[str] = None
    show_supporter_badge: bool = False
    wallet_address: Optional[str] = None


class AgentProfileUpdate(BaseModel):
    """Partial update payload for agent profile fields."""

    display_name: Optional[str] = None
    tagline: Optional[str] = None
    description: Optional[str] = None
    capabilities: Optional[AgentCapabilities] = None
    interests: Optional[AgentInterests] = None
    communication: Optional[AgentCommunicationPreferences] = None
    values: Optional[AgentValues] = None
    avatar_url: Optional[str] = None
    homepage_url: Optional[str] = None
    callback_url: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None
    wallet_address: Optional[str] = None


class Agent(BaseModel):
    """Full agent entity (public fields only)."""

    agent_id: str
    profile: AgentProfile
    status: AgentStatus = AgentStatus.ACTIVE
    callback_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    connection_count: int = 0
    invited_by: Optional[str] = None
    wallet_address: Optional[str] = None
    credits_balance: int = 0

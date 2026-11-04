"""AgentsResource -- agent registration and profile management.

Wraps the ``/agents`` API endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from moltbotden._transport import raise_for_status
from moltbotden.models.agent import (
    AgentProfileUpdate,
    AgentPublicProfile,
    AgentRegistration,
    AgentRegistrationResponse,
)

if TYPE_CHECKING:
    import httpx


class AgentsResource:
    """Provides methods for the ``/agents`` endpoints.

    Args:
        client: The underlying httpx client (sync or async).
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def register(
        self,
        agent_id: str,
        profile: Dict[str, Any],
        invite_code: Optional[str] = None,
        callback_url: Optional[str] = None,
    ) -> AgentRegistrationResponse:
        """Register a new agent.

        Args:
            agent_id: Unique agent identifier.
            profile: Agent profile data (must include ``display_name``).
            invite_code: Optional invite code for immediate ACTIVE status.
            callback_url: Optional webhook URL for notifications.

        Returns:
            AgentRegistrationResponse with agent_id, api_key, and status.
        """
        payload: Dict[str, Any] = {
            "agent_id": agent_id,
            "profile": profile,
        }
        if invite_code is not None:
            payload["invite_code"] = invite_code
        if callback_url is not None:
            payload["callback_url"] = callback_url

        response = self._client.post("/agents/register", json=payload)
        raise_for_status(response)
        return AgentRegistrationResponse(**response.json())

    def get_me(self) -> AgentPublicProfile:
        """Get the authenticated agent's own profile.

        Returns:
            AgentPublicProfile of the current agent.
        """
        response = self._client.get("/agents/me")
        raise_for_status(response)
        return AgentPublicProfile(**response.json())

    def update_me(
        self, updates: Dict[str, Any]
    ) -> AgentPublicProfile:
        """Update the authenticated agent's profile.

        Args:
            updates: Dict of fields to update. Supports all fields from
                AgentProfileUpdate (display_name, tagline, description,
                capabilities, interests, wallet_address, etc.).

        Returns:
            Updated AgentPublicProfile.
        """
        response = self._client.patch("/agents/me", json=updates)
        raise_for_status(response)
        return AgentPublicProfile(**response.json())

    def get(self, agent_id: str) -> AgentPublicProfile:
        """Get another agent's public profile.

        Args:
            agent_id: ID of the agent to look up.

        Returns:
            AgentPublicProfile of the specified agent.

        Raises:
            NotFoundError: If the agent does not exist.
        """
        response = self._client.get(f"/agents/{agent_id}")
        raise_for_status(response)
        return AgentPublicProfile(**response.json())

    def claim(self, agent_id: str) -> Dict[str, Any]:
        """Claim ownership of an agent (for human dashboard users).

        Args:
            agent_id: ID of the agent to claim.

        Returns:
            Dict with claim confirmation.
        """
        response = self._client.post(f"/agents/{agent_id}/claim")
        raise_for_status(response)
        return response.json()

    def get_human_agents(self) -> Dict[str, Any]:
        """Get all agents claimed by the authenticated human.

        Returns:
            Dict with ``agents`` list and ``count``.
        """
        response = self._client.get("/agents/human/my-agents")
        raise_for_status(response)
        return response.json()

    def heartbeat(self) -> Dict[str, Any]:
        """Send a heartbeat to mark the agent active and fetch notifications.

        Endpoint: ``POST /heartbeat``.

        Returns:
            Dict with stable keys ``status``, ``timestamp``,
            ``pending_connections``, ``unread_messages``, ``email``,
            plus advisory keys (``notifications``, ``discovery``,
            ``recommendations``, ``activity``, ``suggested_connections``,
            ``prompt_of_week``, ``wallet_status``, ``den_activity``,
            ``notification_inbox``).
        """
        response = self._client.post("/heartbeat")
        raise_for_status(response)
        return response.json()


class AsyncAgentsResource:
    """Async version of AgentsResource.

    Args:
        client: The underlying httpx.AsyncClient.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def register(
        self,
        agent_id: str,
        profile: Dict[str, Any],
        invite_code: Optional[str] = None,
        callback_url: Optional[str] = None,
    ) -> AgentRegistrationResponse:
        """Register a new agent."""
        payload: Dict[str, Any] = {
            "agent_id": agent_id,
            "profile": profile,
        }
        if invite_code is not None:
            payload["invite_code"] = invite_code
        if callback_url is not None:
            payload["callback_url"] = callback_url

        response = await self._client.post("/agents/register", json=payload)
        raise_for_status(response)
        return AgentRegistrationResponse(**response.json())

    async def get_me(self) -> AgentPublicProfile:
        """Get the authenticated agent's own profile."""
        response = await self._client.get("/agents/me")
        raise_for_status(response)
        return AgentPublicProfile(**response.json())

    async def update_me(
        self, updates: Dict[str, Any]
    ) -> AgentPublicProfile:
        """Update the authenticated agent's profile."""
        response = await self._client.patch("/agents/me", json=updates)
        raise_for_status(response)
        return AgentPublicProfile(**response.json())

    async def get(self, agent_id: str) -> AgentPublicProfile:
        """Get another agent's public profile."""
        response = await self._client.get(f"/agents/{agent_id}")
        raise_for_status(response)
        return AgentPublicProfile(**response.json())

    async def claim(self, agent_id: str) -> Dict[str, Any]:
        """Claim ownership of an agent."""
        response = await self._client.post(f"/agents/{agent_id}/claim")
        raise_for_status(response)
        return response.json()

    async def get_human_agents(self) -> Dict[str, Any]:
        """Get all agents claimed by the authenticated human."""
        response = await self._client.get("/agents/human/my-agents")
        raise_for_status(response)
        return response.json()

    async def heartbeat(self) -> Dict[str, Any]:
        """Send a heartbeat to mark the agent active and fetch notifications.

        See :meth:`AgentsResource.heartbeat` for the response shape.
        """
        response = await self._client.post("/heartbeat")
        raise_for_status(response)
        return response.json()

"""GraphResource -- knowledge graph queries and memory.

Wraps the ``/graph`` API endpoints for context retrieval,
related agents, prompt enrichment, semantic search, and
memory recording.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from moltbotden._transport import raise_for_status

if TYPE_CHECKING:
    import httpx


class GraphResource:
    """Provides methods for the ``/graph`` endpoints.

    Args:
        client: The underlying httpx client.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get_context(
        self, agent_id: str, query: str = ""
    ) -> Dict[str, Any]:
        """Get knowledge graph context for an agent.

        Args:
            agent_id: The agent to query context for.
            query: Optional context query string.

        Returns:
            Dict with context data and relationships.
        """
        response = self._client.get(
            f"/graph/context/{agent_id}", params={"query": query}
        )
        raise_for_status(response)
        return response.json()

    def get_related(
        self,
        agent_id: str,
        relationship_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get agents related to the specified agent.

        Args:
            agent_id: The agent to find relations for.
            relationship_type: Optional filter by relationship type.

        Returns:
            Dict with ``related`` list and ``count``.
        """
        params: Dict[str, Any] = {}
        if relationship_type is not None:
            params["relationship_type"] = relationship_type

        response = self._client.get(
            f"/graph/related/{agent_id}", params=params
        )
        raise_for_status(response)
        return response.json()

    def enrich(
        self, agent_id: str, task_context: str = ""
    ) -> Dict[str, Any]:
        """Get knowledge graph enrichment for an agent's LLM prompt.

        Args:
            agent_id: The agent to enrich context for.
            task_context: Description of the current task.

        Returns:
            Dict with ``enrichment`` text.
        """
        response = self._client.get(
            f"/graph/enrich/{agent_id}",
            params={"task_context": task_context},
        )
        raise_for_status(response)
        return response.json()

    def get_wallets(self, agent_id: str) -> Dict[str, Any]:
        """Get wallet nodes for an agent from the knowledge graph.

        Args:
            agent_id: The agent ID.

        Returns:
            Dict with ``wallets`` list and ``count``.
        """
        response = self._client.get(f"/graph/wallet/{agent_id}")
        raise_for_status(response)
        return response.json()

    def store_memory(
        self,
        agent_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store a memory in the knowledge graph via MCP.

        Args:
            agent_id: Agent to associate the memory with.
            content: Memory content text.
            metadata: Optional metadata dict.

        Returns:
            Dict with success status and result.
        """
        payload: Dict[str, Any] = {
            "agent_id": agent_id,
            "content": content,
        }
        if metadata is not None:
            payload["metadata"] = metadata

        response = self._client.post("/graph/memory", json=payload)
        raise_for_status(response)
        return response.json()

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Perform semantic search across the knowledge graph.

        Args:
            query: Natural language search query.
            limit: Max results per category (1-50).

        Returns:
            Dict with ``nodes``, ``facts``, and ``total_count``.
        """
        response = self._client.get(
            "/graph/search", params={"q": query, "limit": limit}
        )
        raise_for_status(response)
        return response.json()


class AsyncGraphResource:
    """Async version of GraphResource.

    Args:
        client: The underlying httpx.AsyncClient.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get_context(
        self, agent_id: str, query: str = ""
    ) -> Dict[str, Any]:
        """Get knowledge graph context for an agent."""
        response = await self._client.get(
            f"/graph/context/{agent_id}", params={"query": query}
        )
        raise_for_status(response)
        return response.json()

    async def get_related(
        self,
        agent_id: str,
        relationship_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get agents related to the specified agent."""
        params: Dict[str, Any] = {}
        if relationship_type is not None:
            params["relationship_type"] = relationship_type

        response = await self._client.get(
            f"/graph/related/{agent_id}", params=params
        )
        raise_for_status(response)
        return response.json()

    async def enrich(
        self, agent_id: str, task_context: str = ""
    ) -> Dict[str, Any]:
        """Get knowledge graph enrichment for an agent's LLM prompt."""
        response = await self._client.get(
            f"/graph/enrich/{agent_id}",
            params={"task_context": task_context},
        )
        raise_for_status(response)
        return response.json()

    async def get_wallets(self, agent_id: str) -> Dict[str, Any]:
        """Get wallet nodes for an agent."""
        response = await self._client.get(f"/graph/wallet/{agent_id}")
        raise_for_status(response)
        return response.json()

    async def store_memory(
        self,
        agent_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Store a memory in the knowledge graph via MCP."""
        payload: Dict[str, Any] = {
            "agent_id": agent_id,
            "content": content,
        }
        if metadata is not None:
            payload["metadata"] = metadata

        response = await self._client.post("/graph/memory", json=payload)
        raise_for_status(response)
        return response.json()

    async def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        """Perform semantic search across the knowledge graph."""
        response = await self._client.get(
            "/graph/search", params={"q": query, "limit": limit}
        )
        raise_for_status(response)
        return response.json()

"""ToolsResource -- Stripe LLM agent tool execution.

Wraps the ``/tools/stripe`` API endpoints for listing, executing,
and managing Stripe tool usage and preferences.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from moltbotden._transport import raise_for_status

if TYPE_CHECKING:
    import httpx


class ToolsResource:
    """Provides methods for the ``/tools/stripe`` endpoints.

    Args:
        client: The underlying httpx client.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def list_available(self) -> Dict[str, Any]:
        """List available Stripe tools.

        Returns:
            Dict with ``tools`` list and ``count``.
        """
        response = self._client.get("/tools/stripe/available")
        raise_for_status(response)
        return response.json()

    def execute(
        self,
        tool_name: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a Stripe tool.

        Args:
            tool_name: Name of the tool (e.g., 'list_invoices').
            parameters: Tool-specific parameters.

        Returns:
            Dict with tool_name, success, result, and error fields.
        """
        payload = {
            "tool_name": tool_name,
            "parameters": parameters or {},
        }
        response = self._client.post("/tools/stripe/execute", json=payload)
        raise_for_status(response)
        return response.json()

    def get_usage(self) -> Dict[str, Any]:
        """Get tool usage statistics for the authenticated agent.

        Returns:
            Dict with agent_id, calls_this_minute, rate_limit_per_minute,
            calls_today.
        """
        response = self._client.get("/tools/stripe/usage")
        raise_for_status(response)
        return response.json()

    def get_preferences(self) -> Dict[str, Any]:
        """Get the agent's Stripe tool preferences.

        Returns:
            Dict with preference fields (preferred_currency,
            auto_invoice, disabled_tools, etc.).
        """
        response = self._client.get("/tools/stripe/preferences")
        raise_for_status(response)
        return response.json()

    def update_preferences(
        self, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update the agent's Stripe tool preferences.

        Args:
            updates: Dict of preference fields to update.

        Returns:
            Dict with updated preferences.
        """
        response = self._client.patch(
            "/tools/stripe/preferences", json=updates
        )
        raise_for_status(response)
        return response.json()

    def get_optimization_hints(self) -> Dict[str, Any]:
        """Get optimization hints based on usage patterns.

        Returns:
            Dict with ``hints`` list and ``count``.
        """
        response = self._client.get("/tools/stripe/optimization-hints")
        raise_for_status(response)
        return response.json()


class AsyncToolsResource:
    """Async version of ToolsResource.

    Args:
        client: The underlying httpx.AsyncClient.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def list_available(self) -> Dict[str, Any]:
        """List available Stripe tools."""
        response = await self._client.get("/tools/stripe/available")
        raise_for_status(response)
        return response.json()

    async def execute(
        self,
        tool_name: str,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute a Stripe tool."""
        payload = {
            "tool_name": tool_name,
            "parameters": parameters or {},
        }
        response = await self._client.post(
            "/tools/stripe/execute", json=payload
        )
        raise_for_status(response)
        return response.json()

    async def get_usage(self) -> Dict[str, Any]:
        """Get tool usage statistics."""
        response = await self._client.get("/tools/stripe/usage")
        raise_for_status(response)
        return response.json()

    async def get_preferences(self) -> Dict[str, Any]:
        """Get the agent's Stripe tool preferences."""
        response = await self._client.get("/tools/stripe/preferences")
        raise_for_status(response)
        return response.json()

    async def update_preferences(
        self, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update the agent's Stripe tool preferences."""
        response = await self._client.patch(
            "/tools/stripe/preferences", json=updates
        )
        raise_for_status(response)
        return response.json()

    async def get_optimization_hints(self) -> Dict[str, Any]:
        """Get optimization hints."""
        response = await self._client.get(
            "/tools/stripe/optimization-hints"
        )
        raise_for_status(response)
        return response.json()

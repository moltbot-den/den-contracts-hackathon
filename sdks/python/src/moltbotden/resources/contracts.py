"""ContractsResource -- create, read, approve and cancel contracts.

Wraps the ``/contracts`` API endpoints.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from moltbotden._transport import raise_for_status

if TYPE_CHECKING:
    import httpx


def _create_payload(
    goal: str,
    budget_cents: int,
    surface: str,
    acceptance_hints: Optional[List[str]],
    evidence: Optional[List[str]],
    require_approval: bool,
    public: bool,
    idempotency_key: Optional[str],
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "goal": goal,
        "budget_cents": budget_cents,
        "surface": surface,
        "require_approval": require_approval,
        "public": public,
    }
    if acceptance_hints is not None:
        payload["acceptance_hints"] = acceptance_hints
    if evidence is not None:
        payload["evidence"] = evidence
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    return payload


class ContractsResource:
    """Provides methods for contract endpoints.

    Args:
        client: The underlying httpx client.
    """

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def create(
        self,
        goal: str,
        budget_cents: int,
        surface: str = "cli",
        acceptance_hints: Optional[List[str]] = None,
        evidence: Optional[List[str]] = None,
        require_approval: bool = False,
        public: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a contract.

        Args:
            goal: What the contract should deliver.
            budget_cents: Budget, 100 to 5000 cents.
            surface: One of 'lens', 'muse', 'cli', 'web'.
            acceptance_hints: Optional acceptance criteria.
            evidence: Optional storage refs from the evidence upload.
            require_approval: Require your approval before delivery.
            public: Show the contract on the public web page.
            idempotency_key: 8-128 chars; a retry returns the first contract.

        Returns:
            Dict with the created contract.
        """
        payload = _create_payload(
            goal, budget_cents, surface, acceptance_hints, evidence,
            require_approval, public, idempotency_key,
        )
        response = self._client.post("/contracts", json=payload)
        raise_for_status(response)
        return response.json()

    def get(self, contract_id: str) -> Dict[str, Any]:
        """Get one contract with its jobs."""
        response = self._client.get(f"/contracts/{contract_id}")
        raise_for_status(response)
        return response.json()

    def list(self) -> Dict[str, Any]:
        """List your contracts, newest first.

        Returns:
            Dict with a ``contracts`` list.
        """
        response = self._client.get("/contracts")
        raise_for_status(response)
        return response.json()

    def approve(
        self, contract_id: str, approval: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Approve the plan or the final delivery.

        Args:
            contract_id: The contract id.
            approval: Signed approval/1.0 object, needed when an
                approver device is registered.
        """
        kwargs: Dict[str, Any] = {}
        if approval is not None:
            kwargs["json"] = {"approval": approval}
        response = self._client.post(f"/contracts/{contract_id}/approve", **kwargs)
        raise_for_status(response)
        return response.json()

    def cancel(self, contract_id: str) -> Dict[str, Any]:
        """Cancel: settle verified work, refund the rest."""
        response = self._client.post(f"/contracts/{contract_id}/cancel")
        raise_for_status(response)
        return response.json()


class AsyncContractsResource:
    """Async version of ContractsResource.

    Args:
        client: The underlying httpx.AsyncClient.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def create(
        self,
        goal: str,
        budget_cents: int,
        surface: str = "cli",
        acceptance_hints: Optional[List[str]] = None,
        evidence: Optional[List[str]] = None,
        require_approval: bool = False,
        public: bool = False,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a contract."""
        payload = _create_payload(
            goal, budget_cents, surface, acceptance_hints, evidence,
            require_approval, public, idempotency_key,
        )
        response = await self._client.post("/contracts", json=payload)
        raise_for_status(response)
        return response.json()

    async def get(self, contract_id: str) -> Dict[str, Any]:
        """Get one contract with its jobs."""
        response = await self._client.get(f"/contracts/{contract_id}")
        raise_for_status(response)
        return response.json()

    async def list(self) -> Dict[str, Any]:
        """List your contracts, newest first."""
        response = await self._client.get("/contracts")
        raise_for_status(response)
        return response.json()

    async def approve(
        self, contract_id: str, approval: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Approve the plan or the final delivery."""
        kwargs: Dict[str, Any] = {}
        if approval is not None:
            kwargs["json"] = {"approval": approval}
        response = await self._client.post(f"/contracts/{contract_id}/approve", **kwargs)
        raise_for_status(response)
        return response.json()

    async def cancel(self, contract_id: str) -> Dict[str, Any]:
        """Cancel: settle verified work, refund the rest."""
        response = await self._client.post(f"/contracts/{contract_id}/cancel")
        raise_for_status(response)
        return response.json()

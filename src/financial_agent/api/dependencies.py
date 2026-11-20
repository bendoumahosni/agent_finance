"""Dépendances FastAPI donnant accès aux ressources partagées.

Les endpoints ne savent pas où vivent le pool ou l'agent : ils les demandent.
Les tests peuvent alors les remplacer avec `app.dependency_overrides`.

Les alias `Annotated` en bas de fichier sont la forme recommandée par FastAPI :
le type et la dépendance sont déclarés ensemble, et les signatures des
endpoints restent lisibles.
"""

from typing import Annotated, Any

from fastapi import Depends, Request
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from financial_agent.config import Settings, get_settings
from financial_agent.db.pool import DictPool


def get_pool(request: Request) -> DictPool:
    return request.app.state.pool


def get_agent(request: Request) -> Any:
    return request.app.state.agent


def get_checkpointer(request: Request) -> AsyncPostgresSaver:
    return request.app.state.checkpointer
def get_mcp_failures(request: Request) -> dict[str, str]:
    """Serveurs MCP actuellement injoignables (nom -> message)."""
    return request.app.state.mcp_failures

PoolDep = Annotated[DictPool, Depends(get_pool)]
AgentDep = Annotated[Any, Depends(get_agent)]
CheckpointerDep = Annotated[AsyncPostgresSaver, Depends(get_checkpointer)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
McpFailuresDep = Annotated[dict[str, str], Depends(get_mcp_failures)]



 
"""Consultation et suppression de la mémoire longue.

Un profil durable impose trois obligations : l'utilisateur doit pouvoir le
consulter, le corriger et le supprimer. Sans ces endpoints, le Store serait
une collecte de données opaque, ce qui n'est acceptable ni pour l'utilisateur
ni au regard du RGPD.

Comme pour les conversations, l'identité vient du jeton : on ne peut lire ni
supprimer que son propre profil.
"""

import logging

from fastapi import APIRouter, HTTPException, Response, status

from financial_agent.agent import memory
from financial_agent.api.dependencies import StoreDep
from financial_agent.api.schemas import MemoryOut
from financial_agent.auth.verify import CurrentPrincipal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=list[MemoryOut])
async def list_memories(
    principal: CurrentPrincipal,
    store: StoreDep,
) -> list[dict]:
    """Tout ce que l'agent a retenu de l'appelant."""
    return await memory.list_memories(store, principal.subject)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    key: str,
    principal: CurrentPrincipal,
    store: StoreDep,
) -> Response:
    """Supprime un souvenir précis."""
    normalized = memory.normalize_key(key)
    if normalized is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Clé invalide."
        )

    await memory.delete_memory(store, principal.subject, normalized)
    logger.info("Souvenir supprimé user=%s clé=%s", principal.subject, normalized)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_memories(
    principal: CurrentPrincipal,
    store: StoreDep,
) -> Response:
    """Efface tout le profil. C'est la réponse à une demande RGPD."""
    deleted = await memory.delete_all_memories(store, principal.subject)
    logger.info("Profil effacé user=%s souvenirs=%d", principal.subject, deleted)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
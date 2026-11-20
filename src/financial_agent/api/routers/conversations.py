"""Gestion des conversations : lister, consulter, supprimer.

Ces endpoints ne sont pas réalisables avec le seul checkpointer : ses tables
n'ont pas de `user_id`, pas de titre, et leur schéma appartient à LangGraph.
D'où la table applicative `conversations`.
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from financial_agent.agent.runner import extract_text, get_history
from financial_agent.api.dependencies import AgentDep, CheckpointerDep, PoolDep
from financial_agent.api.schemas import ConversationOut, MessageOut
from financial_agent.auth.verify import CurrentPrincipal
from financial_agent.db.repositories import conversations as repo
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])

# Plafond côté serveur : une liste sans limite est une panne qui attend son
# heure. C'est le serveur qui décide, jamais le client.
LimitQuery = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    principal: CurrentPrincipal,
    pool: PoolDep,
    limit: LimitQuery = 20,
) -> list[dict]:
    """Liste les conversations de l'appelant, la plus récente d'abord."""
    return await repo.list_conversations(pool, principal.subject, limit)


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    principal: CurrentPrincipal,
    pool: PoolDep,
) -> dict:
    conversation = await repo.get_conversation(pool, conversation_id, principal.subject)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation introuvable.",
        )
    return conversation

@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def get_conversation_messages(
    conversation_id: uuid.UUID,
    principal: CurrentPrincipal,
    pool: PoolDep,
    agent: AgentDep,
) -> list[MessageOut]:
    """Historique des messages d'une conversation.

    L'appartenance est vérifiée AVANT de lire l'état : sans ce contrôle,
    n'importe qui connaissant un UUID lirait la conversation d'autrui, car le
    checkpointer ne sait pas à qui appartient un thread.
    """
    if await repo.get_conversation(pool, conversation_id, principal.subject) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation introuvable.",
        )

    messages = await get_history(agent, str(conversation_id))

    return [
        MessageOut(
            role=message.type,
            content=extract_text(message.content),
            tool_calls=[
                call["name"] for call in getattr(message, "tool_calls", None) or []
            ]
            or None,
        )
        for message in messages
    ]


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID,
    principal: CurrentPrincipal,
    pool: PoolDep,
    checkpointer: CheckpointerDep,
) -> Response:
    """Supprime une conversation et l'état de l'agent associé.

    Ordre délibéré : la ligne applicative d'abord, les checkpoints ensuite.
    La suppression n'est pas atomique entre vos tables et celles de LangGraph ;
    si la seconde étape échoue, il reste des checkpoints orphelins, invisibles
    pour l'utilisateur et nettoyables par une tâche de fond. L'ordre inverse
    donnerait une conversation visible mais vide, ce qui est pire.
    """
    deleted = await repo.delete_conversation(pool, conversation_id, principal.subject)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation introuvable.",
        )

    # Supprime checkpoints, checkpoint_blobs et checkpoint_writes du thread.
    await checkpointer.adelete_thread(str(conversation_id))

    logger.info(
        "Conversation supprimée user=%s conv=%s", principal.subject, conversation_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)



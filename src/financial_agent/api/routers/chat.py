"""Endpoint de conversation avec l'agent."""

import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from langchain.agents.middleware import PIIDetectionError

from financial_agent.agent.guardrails import (
    PaymentDataRejected,
    check_user_input,
    redaction_warnings,
    sanitize_user_input,
)
from financial_agent.agent.mcp_tools import warnings_for
from financial_agent.agent.runner import run_financial_agent
from financial_agent.api.dependencies import (
    AgentDep,
    McpFailuresDep,
    PoolDep,
    SettingsDep,
)
from financial_agent.api.schemas import ChatRequest, ChatResponse
from financial_agent.auth.verify import CurrentPrincipal
from financial_agent.db.repositories import conversations as repo

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

TITLE_MAX_LENGTH = 60


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    principal: CurrentPrincipal,
    pool: PoolDep,
    agent: AgentDep,
    settings: SettingsDep,
    mcp_failures: McpFailuresDep,
) -> ChatResponse:
    """Envoie un message à l'agent.

    Sans `conversation_id`, une nouvelle conversation est créée et son
    identifiant est renvoyé : c'est le serveur qui le génère, jamais le client.

    Ordre des opérations : vérifier, puis agir, puis enregistrer. Les contrôles
    bon marché passent avant l'appel au modèle, qui est lent et payant.
    """
    user_id = principal.subject

    # Guardrail d'entrée, AVANT toute écriture. LangGraph enregistre le
    # message dans le checkpoint dès l'entrée dans le graphe : un contrôle
    # fait par un middleware arriverait trop tard, le numéro serait déjà en
    # base. Ici, rien n'est écrit : ni conversation, ni checkpoint.
    try:
        check_user_input(body.message)
    except PaymentDataRejected as exc:
        logger.warning(
            "Guardrail entrée : données de paiement refusées user=%s", user_id
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Votre message semble contenir des données de paiement. "
            "Retirez-les et réessayez : cet agent ne traite pas ces données.",
        ) from exc

    # Rédaction des données personnelles détectables. C'est le texte rédigé
    # qui sera stocké, envoyé au modèle et relu par l'endpoint des messages :
    # la donnée d'origine n'existe nulle part côté serveur.
    message, redacted = sanitize_user_input(body.message)
    if redacted:
        logger.info(
            "Rédaction appliquée user=%s catégories=%s", user_id, ",".join(redacted)
        )

    if body.conversation_id is None:
        conversation_id = uuid.uuid4()
        # Le titre vient du message rédigé, sinon la donnée réapparaîtrait
        # dans la liste des conversations.
        await repo.create_conversation(
            pool, conversation_id, user_id, message[:TITLE_MAX_LENGTH]
        )
    else:
        owned = await repo.get_conversation(pool, body.conversation_id, user_id)
        if owned is None:
            # 404 et non 403 : un 403 révélerait qu'une conversation existe
            # chez un autre utilisateur, et permettrait de les énumérer.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation introuvable.",
            )
        conversation_id = body.conversation_id

    try:
        reply = await asyncio.wait_for(
            run_financial_agent(agent, message, str(conversation_id)),
            timeout=settings.agent_timeout_seconds,
        )
    except PIIDetectionError as exc:
        # Filet de sécurité : PIIMiddleware reste en place en défense en
        # profondeur. Le contrôle ci-dessus devrait avoir déjà refusé, donc
        # si on passe ici, c'est que le détecteur a vu quelque chose que la
        # frontière a laissé filer. On le journalise comme tel.
        logger.warning(
            "Guardrail tardif : PII détectée par le middleware user=%s", user_id
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Votre message semble contenir des données de paiement. "
            "Retirez-les et réessayez : cet agent ne traite pas ces données.",
        ) from exc
    except TimeoutError as exc:
        # Depuis Python 3.11, asyncio.TimeoutError est TimeoutError.
        logger.error("Timeout agent user=%s conv=%s", user_id, conversation_id)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="L'agent a mis trop de temps à répondre.",
        ) from exc
    except Exception as exc:
        # Le détail et la trace vont dans les logs, pas dans la réponse.
        logger.exception("Erreur agent user=%s conv=%s", user_id, conversation_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur lors de l'exécution de l'agent financier.",
        ) from exc

    # Mise à jour après succès : un échange échoué ne remonte pas la
    # conversation en tête de liste.
    await repo.touch_conversation(pool, conversation_id)

    return ChatResponse(
        conversation_id=conversation_id,
        response=reply.text,
        # Trois sources d'avertissements : capacités indisponibles (MCP),
        # données personnelles retirées, et guardrails déclenchés pendant
        # l'exécution.
        warnings=[
            *warnings_for(mcp_failures),
            *redaction_warnings(redacted),
            *reply.warnings,
        ],
    )
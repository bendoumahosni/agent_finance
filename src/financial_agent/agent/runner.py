"""Exécution de l'agent et lecture de son état."""

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage
from financial_agent.observability.tracing import trace_config
from collections.abc import AsyncIterator
logger = logging.getLogger(__name__)


@dataclass
class AgentReply:
    """Réponse de l'agent et avertissements produits pendant l'exécution.

    Un simple `str` ne suffit plus : les guardrails doivent pouvoir remonter
    une information à l'utilisateur.
    """

    text: str
    warnings: list[str] = field(default_factory=list)


def extract_text(content: Any) -> str:
    """Texte d'un message, que le contenu soit une chaîne ou une liste de blocs.

    Fonction pure : réutilisée par le runner et par la lecture d'historique.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)

@dataclass
class StreamEvent:
    """Un événement de la diffusion, indépendant du transport.

    Le runner produit des événements métier ; c'est la couche API qui les
    met au format SSE. Ainsi le même flux pourrait être servi en WebSocket
    sans toucher à l'agent.
    """

    event: str  # "token" | "tool" | "tool_done" | "done"
    data: dict[str, Any]


async def stream_financial_agent(
    agent: Any, message: str, thread_id: str, user_id: str
) -> AsyncIterator[StreamEvent]:
    """Diffuse la réponse de l'agent au fil de sa génération.

    Mêmes garanties que `run_financial_agent` (mémoire, guardrails, traçage) :
    seule la restitution change.

    On ne diffuse pas le contenu des résultats d'outils : ce sont des données
    brutes, non validées, parfois volumineuses. Le client reçoit le nom de
    l'outil, ce qui suffit à afficher « recherche en cours ».
    """
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id, "user_id": user_id},
        **trace_config(thread_id, user_id),
    }

    async for mode, payload in agent.astream(
        {"messages": [HumanMessage(content=message)], "guardrail_warnings": []},
        config=config,
        stream_mode=["messages", "updates"],
    ):
        if mode != "messages":
            continue

        chunk, metadata = payload
        node = metadata.get("langgraph_node")

        if node == "tools":
            # Fin d'un outil : on signale, sans transmettre le contenu.
            yield StreamEvent(
                event="tool_done", data={"name": getattr(chunk, "name", None)}
            )
            continue

        tool_calls = getattr(chunk, "tool_calls", None)
        if tool_calls:
            for call in tool_calls:
                yield StreamEvent(event="tool", data={"name": call["name"]})
            continue

        text = extract_text(chunk.content)
        if text:
            yield StreamEvent(event="token", data={"text": text})

    # Les avertissements de guardrails ne sont connus qu'à la fin : ils sont
    # posés dans l'état par le middleware pendant l'exécution.
    snapshot = await agent.aget_state({"configurable": {"thread_id": thread_id}})
    warnings = list(snapshot.values.get("guardrail_warnings") or [])
    yield StreamEvent(event="done", data={"warnings": warnings})

    
async def run_financial_agent(
    agent: Any, message: str, thread_id: str, user_id: str
) -> AgentReply:
    """Envoie un message à l'agent et renvoie sa réponse.

    `thread_id` est la clé de la mémoire courte : le checkpointer recharge
    l'historique de cette conversation avant d'appeler le modèle.

    `user_id` est la clé de la mémoire longue. Il passe par la configuration
    d'exécution et non par un argument d'outil : ainsi le modèle ne peut pas
    le choisir, et une injection ne peut pas atteindre le profil d'un autre
    utilisateur.

    `guardrail_warnings` est remis à zéro à chaque appel : sans cela, un
    avertissement d'un échange précédent resterait dans l'état et serait
    réaffiché indéfiniment.
    """
    # `trace_config` renvoie un dictionnaire vide si le traçage est
    # désactivé : le runner n'a pas à savoir si Langfuse est configuré.
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id, "user_id": user_id},
        **trace_config(thread_id, user_id),
    }

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)], "guardrail_warnings": []},
        config=config,
    )
    return AgentReply(
        text=extract_text(result["messages"][-1].content),
        warnings=list(result.get("guardrail_warnings") or []),
    )

async def get_history(agent: Any, thread_id: str) -> list[Any]:
    """Messages enregistrés pour cette conversation, dans l'ordre.

    Lus via LangGraph et non en SQL : les messages sont sérialisés dans
    `checkpoint_blobs`, et ce schéma appartient au framework.

    Pas de try/except ici : si la base est injoignable, l'exception doit
    remonter et produire un 500. La convertir en liste vide ferait croire
    au client que la conversation est vide.
    """
    snapshot = await agent.aget_state({"configurable": {"thread_id": thread_id}})
    return snapshot.values.get("messages", [])
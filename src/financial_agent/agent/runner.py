"""Exécution de l'agent et lecture de son état."""

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage

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


async def run_financial_agent(
    agent: Any, message: str, thread_id: str
) -> AgentReply:
    """Envoie un message à l'agent et renvoie sa réponse.

    `thread_id` est la clé de la mémoire : le checkpointer recharge
    l'historique de cette conversation avant d'appeler le modèle.

    `guardrail_warnings` est remis à zéro à chaque appel : sans cela, un
    avertissement d'un échange précédent resterait dans l'état et serait
    réaffiché indéfiniment.
    """
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content=message)], "guardrail_warnings": []},
        config={"configurable": {"thread_id": thread_id}},
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
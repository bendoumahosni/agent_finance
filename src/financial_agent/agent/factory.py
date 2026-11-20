"""Construction de l'agent.

L'agent reçoit son checkpointer en paramètre (injection de dépendances) : il ne
sait pas s'il s'agit de Postgres, de SQLite ou de la mémoire. Les tests peuvent
donc lui passer un `InMemorySaver` sans base de données.
"""

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    PIIMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from financial_agent.agent.guardrails import ToolResultGuardMiddleware
from financial_agent.agent.prompts import SYSTEM_PROMPT
from financial_agent.agent.tools.calcul import calcul_tool
from financial_agent.agent.tools.search import build_search_tool
from financial_agent.config import Settings


def build_llm(settings: Settings) -> ChatOpenAI:
    kwargs: dict = {
        "model_name": settings.model_name,
        "openai_api_key": settings.openai_api_key,
    }
    # Laissé à None par défaut : langchain-openai choisit l'API adaptée au
    # modèle. Mettre False force l'API Chat Completions, utile si la
    # conversion vers l'API Responses pose problème.
    if settings.use_responses_api is not None:
        kwargs["use_responses_api"] = settings.use_responses_api
    return ChatOpenAI(**kwargs)


def build_middleware(settings: Settings, llm: ChatOpenAI) -> list[Any]:
    """Guardrails et gestion de l'historique.

    L'ordre compte : le premier déclaré est le plus externe.

    Type de retour `list[Any]` : chaque middleware est paramétré par son
    propre schéma d'état, et une liste hétérogène ne satisfait pas
    `list[AgentMiddleware[...]]` côté typage.
    """
    return [
        # 1. Cas net, bloqué en entrée : aucune raison légitime d'envoyer un
        #    numéro de carte. Surtout, cela évite qu'il atterrisse dans les
        #    checkpoints Postgres et dans les logs. Lève PIIDetectionError,
        #    que l'endpoint traduit en 400.
        PIIMiddleware("credit_card", strategy="block", apply_to_input=True),
        # 2. Plafonds : bornent le coût et empêchent les boucles.
        #    exit_behavior="end" arrête proprement plutôt que de lever.
        ModelCallLimitMiddleware(
            run_limit=settings.max_model_calls_per_run, exit_behavior="end"
        ),
        ToolCallLimitMiddleware(
            run_limit=settings.max_tool_calls_per_run, exit_behavior="end"
        ),
        # 3. Taille de l'historique : au-delà du seuil, les anciens messages
        #    sont remplacés par un résumé. Sans cela, le coût et la latence
        #    croissent indéfiniment avec la longueur de la conversation.
        SummarizationMiddleware(
            model=llm,
            trigger=("messages", settings.summarize_after_messages),
            keep=("messages", settings.summarize_keep_messages),
        ),
        # 4. Inspection des résultats d'outils : le contenu non validé
        #    (web, MCP) entre ici. Le plus utile des quatre.
        ToolResultGuardMiddleware(),
    ]


def build_agent(
    checkpointer,
    settings: Settings,
    extra_tools: list[BaseTool] | None = None,
):
    """Crée l'agent financier.

    `extra_tools` reçoit les outils découverts sur les serveurs MCP. Ils
    s'ajoutent aux outils locaux ; la valeur par défaut `None` garde le
    comportement d'origine quand aucun serveur n'est configuré.
    """
    # La conversion de devises n'est plus un outil local : elle est fournie
    # par le serveur MCP "currency". Si ce serveur est injoignable, la
    # capacité disparaît réellement, et /chat le signale dans `warnings`.
    tools: list[BaseTool] = [build_search_tool(settings), calcul_tool]
    if extra_tools:
        tools.extend(extra_tools)

    llm = build_llm(settings)

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        middleware=build_middleware(settings, llm),
    )
"""Chargement des outils exposés par des serveurs MCP.

Ce fichier ne s'appelle pas `mcp.py` : c'est le nom du SDK installé, et la
confusion en lecture serait garantie.

Transport HTTP (`streamable_http`) et non stdio : les serveurs sont des
services à part, joignables par leur nom dans le réseau Docker, et non des
sous-processus de l'API.
"""

import logging

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection, StreamableHttpConnection

from financial_agent.config import Settings

logger = logging.getLogger(__name__)

MCP_TIMEOUT_SECONDS = 10.0
def describe_error(exc: BaseException) -> str:
    """Message lisible pour une exception, y compris un ExceptionGroup.

    Le client MCP s'exécute dans un TaskGroup : `str(exc)` donne alors
    « unhandled errors in a TaskGroup », qui ne dit rien de la cause. On
    descend dans les sous-exceptions pour journaliser l'erreur réelle.
    """
    if isinstance(exc, BaseExceptionGroup):
        return "; ".join(describe_error(sub) for sub in exc.exceptions)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__

def build_connections(settings: Settings) -> dict[str, Connection]:
    """Traduit la configuration en connexions attendues par le client MCP.

    `StreamableHttpConnection` est un TypedDict du paquet : l'utiliser plutôt
    qu'un dictionnaire nu fait vérifier les clés par le typage.
    """
    return {
        name: StreamableHttpConnection(
            transport="streamable_http",
            url=url,
            timeout=MCP_TIMEOUT_SECONDS,
        )
        for name, url in settings.mcp_servers.items()
    }


async def load_mcp_tools(
    settings: Settings, *, verbose: bool = True
) -> tuple[list[BaseTool], dict[str, str]]:
    """Récupère les outils de chaque serveur MCP configuré.

    Renvoie les outils chargés et les serveurs en échec (nom -> message).

    Les serveurs sont interrogés **un par un** : un appel global à
    `get_tools()` échouerait entièrement dès qu'un seul serveur est
    injoignable, alors qu'on veut une dégradation partielle.
    """
    connections = build_connections(settings)
    if not connections:
        return [], {}

    # tool_name_prefix : préfixe les outils par le nom du serveur, pour éviter
    # les collisions si deux serveurs exposent un outil de même nom.
    client = MultiServerMCPClient(connections, tool_name_prefix=True)

    tools: list[BaseTool] = []
    failures: dict[str, str] = {}

    for name in connections:
        try:
            server_tools = await client.get_tools(server_name=name)
            print("les  serveurs mcp",server_tools)
        except Exception as exc:
            reason = describe_error(exc)
            logger.log(
                logging.WARNING if verbose else logging.DEBUG,
                "Serveur MCP '%s' injoignable : %s",
                name,
                reason,
            )
            failures[name] = reason
            continue
        logger.info("Serveur MCP '%s' : %d outil(s) chargé(s)", name, len(server_tools))
        tools.extend(server_tools)

    return tools, failures


def warnings_for(failures: dict[str, str]) -> list[str]:
    """Messages destinés à l'utilisateur final, pas aux logs.

    On nomme la capacité indisponible sans exposer la cause technique.
    """
    return [
        f"Le serveur d'outils « {name} » est momentanément indisponible : "
        "certaines capacités de l'agent sont désactivées."
        for name in sorted(failures)
    ]
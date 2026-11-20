"""Outil de recherche web (Tavily).

L'outil est construit par une fonction et non à l'import : il a besoin d'une
clé d'API, donc importer ce module ne doit rien exiger de l'environnement.
"""

from langchain_tavily import TavilySearch

from financial_agent.config import Settings


def build_search_tool(settings: Settings) -> TavilySearch:
    return TavilySearch(
        max_results=settings.search_max_results,
        tavily_api_key=settings.tavily_api_key,
    )

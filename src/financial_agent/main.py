"""Point d'entrée de l'application.

`create_app()` est une fabrique : les tests peuvent créer une application avec
un cycle de vie différent (sans base de données) au lieu de subir un objet
global construit à l'import.
"""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from financial_agent import __version__
from financial_agent.agent.factory import build_agent
from financial_agent.agent.mcp_tools import load_mcp_tools
from financial_agent.api.routers import chat, conversations, health
from financial_agent.config import Settings, get_settings
from financial_agent.db.pool import create_pool
from financial_agent.logging_setup import setup_logging

logger = logging.getLogger(__name__)

async def reload_mcp_tools(
    app: FastAPI, settings: Settings, *, verbose: bool = False
) -> bool:
    """Recharge les outils MCP et reconstruit l'agent si quelque chose a changé.

    Fonction séparée de la boucle pour être testable sans minuteur.
    Renvoie True si l'agent a été reconstruit.
    """
    tools, failures = await load_mcp_tools(settings, verbose=verbose)

    unchanged = {tool.name for tool in tools} == {
        tool.name for tool in app.state.mcp_tools
    } and failures.keys() == app.state.mcp_failures.keys()
    if unchanged:
        return False

    app.state.mcp_tools = tools
    app.state.mcp_failures = failures
    # Réaffectation : les requêtes en cours gardent l'ancien agent,
    # les suivantes prennent le nouveau.
    app.state.agent = build_agent(app.state.checkpointer, settings, extra_tools=tools)

    logger.info(
        "Outils MCP rechargés : %d outil(s), %d serveur(s) en échec",
        len(tools),
        len(failures),
    )
    return True

async def mcp_retry_loop(app: FastAPI, settings: Settings) -> None:
    """Retente périodiquement les serveurs MCP injoignables.

    Le corps est protégé : une exception non attrapée tuerait la tâche
    silencieusement, et plus aucune tentative n'aurait lieu.
    """
    while True:
        await asyncio.sleep(settings.mcp_retry_seconds)
        if not app.state.mcp_failures:
            continue
        try:
            await reload_mcp_tools(app, settings)
        except Exception:
            logger.exception("Échec du rechargement des outils MCP")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ouvre les ressources au démarrage, les ferme à l'arrêt.

    Celui qui ouvre une ressource doit être celui qui la ferme, et seule
    l'application connaît ces deux moments.
    """
    settings = get_settings()
    setup_logging(settings.log_level)

    # --- DÉMARRAGE ---
    async with create_pool(settings) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()  # tables du checkpointer, sans effet si présentes

        app.state.pool = pool
        app.state.checkpointer = checkpointer

        # Démarrage dégradé : un serveur MCP injoignable n'empêche pas
        # l'application de servir, l'utilisateur en est averti dans /chat.
        mcp_tools, mcp_failures = await load_mcp_tools(settings)
        app.state.mcp_tools = mcp_tools
        app.state.mcp_failures = mcp_failures
        app.state.agent = build_agent(checkpointer, settings, extra_tools=mcp_tools)

        retry_task: asyncio.Task | None = None
        if settings.mcp_servers:
            # create_task : on garde la référence, sinon le ramasse-miettes
            # peut supprimer la tâche.
            retry_task = asyncio.create_task(mcp_retry_loop(app, settings))

        logger.info(
            "Agent prêt (%d outil(s) MCP, %d serveur(s) en échec), pool ouvert.",
            len(mcp_tools),
            len(mcp_failures),
        )

        try:
            yield
        finally:
            # --- ARRÊT ---
            if retry_task is not None:
                retry_task.cancel()
                with suppress(asyncio.CancelledError):
                    await retry_task
        # le "async with" ferme le pool

    logger.info("Pool Postgres fermé.")

def create_app(lifespan_handler=lifespan) -> FastAPI:
    app = FastAPI(
        title="Financial Agent API",
        description="API de conversation avec un agent financier",
        version=__version__,
        lifespan=lifespan_handler,
    )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(conversations.router)

    return app


app = create_app()

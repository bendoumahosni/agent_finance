"""Pool de connexions Postgres.

Un pool, et non une connexion unique : des requêtes simultanées ne doivent
pas se bloquer, et ouvrir une connexion par requête coûte cher.

Il est créé par le cycle de vie de l'application (`lifespan`), pas à l'import :
une ressource asynchrone a besoin d'une boucle d'événements active, et celui
qui ouvre une ressource doit être celui qui la ferme.
"""

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from financial_agent.config import Settings

# Le pool renvoie des lignes sous forme de dictionnaires (row_factory).
# Le dire dans le type évite que les couches au-dessus croient manipuler
# des tuples, et c'est ce que `AsyncPostgresSaver` attend.
DictPool = AsyncConnectionPool[AsyncConnection[DictRow]]


def create_pool(settings: Settings) -> DictPool:
    return AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        # Sans délai, une requête attendrait indéfiniment si toutes les
        # connexions sont prises : on préfère une erreur claire.
        timeout=settings.pool_timeout_seconds,
        # Écarte les connexions mortes (coupure réseau, redémarrage de Postgres).
        check=AsyncConnectionPool.check_connection,
        kwargs={
            # autocommit et dict_row sont exigés par AsyncPostgresSaver.
            "autocommit": True,
            "row_factory": dict_row,
            # Désactive les requêtes préparées côté serveur : nécessaire
            # derrière un proxy de connexions comme PgBouncer.
            "prepare_threshold": 0,
        },
        # Ouvert par le gestionnaire de contexte du lifespan.
        open=False,
    )

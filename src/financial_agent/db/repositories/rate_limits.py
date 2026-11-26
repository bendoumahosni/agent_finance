"""Compteur de requêtes par utilisateur, en fenêtre fixe.

Pourquoi en base et non en mémoire : avec plusieurs instances de l'API, un
compteur par processus laisserait passer `replicas × limite`. C'est la même
erreur que `InMemorySaver` pour la mémoire de conversation.

Pourquoi Postgres et non Redis : la base est déjà là, et le volume d'écriture
est négligeable devant les appels de modèle que l'on cherche à limiter. Redis
devient le bon choix à fort trafic, car ces écritures finissent par peser sur
une base qui sert aussi le métier.

La table ne grossit pas : une ligne par utilisateur, réutilisée d'une fenêtre
à l'autre. Pas de purge à prévoir, contrairement à une table qui garderait une
ligne par fenêtre.
"""

import logging

from financial_agent.db.pool import DictPool

logger = logging.getLogger(__name__)

# Une seule instruction, donc atomique : l'incrément et la remise à zéro de
# fenêtre ne peuvent pas s'entrelacer entre deux requêtes simultanées.
# Un SELECT puis un UPDATE séparés perdraient des incréments sous charge.
_HIT_SQL = """
WITH w AS (
    SELECT to_timestamp(floor(extract(epoch FROM now()) / %(window)s) * %(window)s) AS s
)
INSERT INTO rate_limits (user_id, window_start, hits)
SELECT %(user_id)s, w.s, 1 FROM w
ON CONFLICT (user_id) DO UPDATE SET
    hits = CASE
        WHEN rate_limits.window_start = (SELECT s FROM w)
        THEN rate_limits.hits + 1
        ELSE 1
    END,
    window_start = (SELECT s FROM w)
RETURNING
    hits,
    ceil(
        extract(EPOCH FROM (
            window_start + make_interval(secs => %(window)s) - now()
        ))
    )::int AS retry_after
"""


async def hit(pool: DictPool, user_id: str, window_seconds: int) -> tuple[int, int]:
    """Enregistre une requête de cet utilisateur.

    Renvoie le nombre de requêtes dans la fenêtre courante et le nombre de
    secondes avant qu'elle ne se referme (pour l'en-tête `Retry-After`).
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            _HIT_SQL, {"user_id": user_id, "window": window_seconds}
        )
        row = await cur.fetchone()

    if row is None:  # pragma: no cover - RETURNING garantit une ligne
        return 0, 0
    return int(row["hits"]), max(1, int(row["retry_after"]))
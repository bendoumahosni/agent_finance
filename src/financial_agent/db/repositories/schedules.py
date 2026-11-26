"""Tâches planifiées : un prompt rejoué chaque jour à une heure locale.

Périmètre assumé : une exécution quotidienne à une heure donnée, pas une
expression cron complète. Cela couvre le besoin (« chaque jour à 8h30 ») sans
introduire un analyseur d'expressions et ses cas limites.

L'heure est stockée avec son **fuseau**, jamais en heure naïve : 8h30 à Paris
ne tombe pas au même instant UTC en janvier et en juillet. Le calcul de la
prochaine échéance est fait par Postgres avec `AT TIME ZONE`, qui applique
l'offset correct à la date concernée.
"""

import logging
import uuid

from psycopg.rows import DictRow

from financial_agent.db.pool import DictPool

logger = logging.getLogger(__name__)

# Prochaine occurrence de l'heure locale, ramenée en UTC. Si elle est déjà
# passée aujourd'hui, c'est demain.
_NEXT_RUN_EXPR = """
    CASE
        WHEN ((date_trunc('day', now() AT TIME ZONE %(tz)s) + %(run_at)s::time)
              AT TIME ZONE %(tz)s) > now()
        THEN ((date_trunc('day', now() AT TIME ZONE %(tz)s) + %(run_at)s::time)
              AT TIME ZONE %(tz)s)
        ELSE ((date_trunc('day', now() AT TIME ZONE %(tz)s) + %(run_at)s::time)
              AT TIME ZONE %(tz)s) + interval '1 day'
    END
"""

# Prise de tâches dues, en une seule instruction donc atomique. Le
# sous-SELECT verrouille les lignes choisies et `SKIP LOCKED` fait que deux
# instances prennent des lots disjoints : une tâche quotidienne ne part pas
# en double parce qu'il y a deux replicas.
#
# L'échéance est repoussée AU MOMENT de la prise, avant l'exécution : si le
# processus meurt en cours de route, la tâche ne se rejoue pas en boucle.
_CLAIM_SQL = """
UPDATE scheduled_tasks SET
    next_run_at = (
        ((date_trunc('day', now() AT TIME ZONE timezone) + run_at_time)
         AT TIME ZONE timezone) + interval '1 day'
    ),
    last_run_at = now()
WHERE id IN (
    SELECT id FROM scheduled_tasks
    WHERE enabled AND next_run_at <= now()
    ORDER BY next_run_at
    LIMIT %(limit)s
    FOR UPDATE SKIP LOCKED
)
RETURNING id, user_id, prompt, email_id
"""


async def create(
    pool: DictPool,
    user_id: str,
    prompt: str,
    email_id: uuid.UUID,
    run_at_time: str,
    timezone: str,
) -> DictRow:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"""
            INSERT INTO scheduled_tasks
                (id, user_id, prompt, email_id, run_at_time, timezone, next_run_at)
            VALUES
                (%(id)s, %(user_id)s, %(prompt)s, %(email_id)s,
                 %(run_at)s::time, %(tz)s, {_NEXT_RUN_EXPR})
            RETURNING id, prompt, run_at_time, timezone, enabled,
                      next_run_at, last_run_at, last_error, created_at
            """,
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "prompt": prompt,
                "email_id": email_id,
                "run_at": run_at_time,
                "tz": timezone,
            },
        )
        row = await cur.fetchone()
    assert row is not None
    return row


async def list_for_user(pool: DictPool, user_id: str) -> list[DictRow]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, prompt, run_at_time, timezone, enabled,
                   next_run_at, last_run_at, last_error, created_at
            FROM scheduled_tasks
            WHERE user_id = %s
            ORDER BY created_at
            """,
            (user_id,),
        )
        return await cur.fetchall()


async def count_for_user(pool: DictPool, user_id: str) -> int:
    """Nombre de tâches, pour borner la consommation : chaque tâche
    quotidienne coûte des appels de modèle sans que personne ne regarde."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM scheduled_tasks WHERE user_id = %s",
            (user_id,),
        )
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def delete(pool: DictPool, task_id: uuid.UUID, user_id: str) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM scheduled_tasks WHERE id = %s AND user_id = %s RETURNING id",
            (task_id, user_id),
        )
        return await cur.fetchone() is not None


async def enable(pool: DictPool, task_id: uuid.UUID, user_id: str) -> bool:
    """Réactive une tâche désactivée après un échec, et replanifie.

    Sans cet endpoint, une tâche désactivée serait définitivement perdue :
    c'est la contrepartie obligatoire du choix « désactiver au premier
    échec ».
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            UPDATE scheduled_tasks SET
                enabled = true,
                last_error = NULL,
                next_run_at = (
                    CASE
                        WHEN ((date_trunc('day', now() AT TIME ZONE timezone)
                               + run_at_time) AT TIME ZONE timezone) > now()
                        THEN ((date_trunc('day', now() AT TIME ZONE timezone)
                               + run_at_time) AT TIME ZONE timezone)
                        ELSE ((date_trunc('day', now() AT TIME ZONE timezone)
                               + run_at_time) AT TIME ZONE timezone)
                             + interval '1 day'
                    END
                )
            WHERE id = %s AND user_id = %s
            RETURNING id
            """,
            (task_id, user_id),
        )
        return await cur.fetchone() is not None


async def claim_due(pool: DictPool, limit: int) -> list[DictRow]:
    """Prend les tâches à exécuter et les replanifie au lendemain."""
    async with pool.connection() as conn:
        cur = await conn.execute(_CLAIM_SQL, {"limit": limit})
        return await cur.fetchall()


async def disable_with_error(pool: DictPool, task_id: uuid.UUID, error: str) -> None:
    """Désactive la tâche et conserve la cause.

    Décision assumée : on désactive au premier échec plutôt que de réessayer
    indéfiniment. Une tâche qui échoue en silence pendant des semaines est
    pire qu'une tâche arrêtée dont l'utilisateur est averti. La contrepartie
    est l'endpoint de réactivation.
    """
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE scheduled_tasks SET enabled = false, last_error = %s WHERE id = %s",
            (error[:500], task_id),
        )


async def clear_error(pool: DictPool, task_id: uuid.UUID) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE scheduled_tasks SET last_error = NULL WHERE id = %s",
            (task_id,),
        )
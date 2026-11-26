"""File de sortie des e-mails.

C'est une **file**, pas un journal : une ligne envoyée est supprimée. Elle
contient une adresse et un corps de message, donc des données personnelles
qu'il n'y a aucune raison de conserver après l'envoi. Si vous voulez un jour
une piste d'audit, elle se fera dans une table dédiée qui ne garde pas le
corps.

Pourquoi une file plutôt qu'un envoi direct dans la requête HTTP : un
fournisseur lent ou indisponible rendrait l'endpoint lent puis échouerait.
Ici l'endpoint écrit une ligne et répond ; une boucle de fond envoie et
réessaie.
"""

import logging
import uuid

from psycopg.rows import DictRow

from financial_agent.db.pool import DictPool

logger = logging.getLogger(__name__)

# Prise de messages dus. Même schéma que les tâches planifiées :
# `FOR UPDATE SKIP LOCKED` dans un sous-SELECT, et l'échéance est repoussée
# dans la même instruction. Deux instances ne peuvent donc pas envoyer deux
# fois le même message.
_CLAIM_SQL = """
UPDATE outbox SET
    attempts = attempts + 1,
    next_attempt_at = now() + make_interval(
        secs => %(backoff)s * power(2, attempts)
    )
WHERE id IN (
    SELECT id FROM outbox
    WHERE next_attempt_at <= now()
    ORDER BY next_attempt_at
    LIMIT %(limit)s
    FOR UPDATE SKIP LOCKED
)
RETURNING id, to_address, subject, body, attempts
"""


async def enqueue(
    pool: DictPool, to_address: str, subject: str, body: str
) -> uuid.UUID:
    """Met un message en file. L'envoi aura lieu dans la boucle de fond."""
    message_id = uuid.uuid4()
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO outbox (id, to_address, subject, body)
            VALUES (%s, %s, %s, %s)
            """,
            (message_id, to_address, subject, body),
        )
    return message_id


async def claim_due(pool: DictPool, limit: int, backoff_seconds: int) -> list[DictRow]:
    """Prend les messages à envoyer et programme leur prochain essai.

    L'échéance est repoussée AVANT l'envoi : si le processus meurt pendant
    l'envoi, le message sera réessayé plus tard plutôt que repris
    immédiatement en boucle.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            _CLAIM_SQL, {"limit": limit, "backoff": backoff_seconds}
        )
        return await cur.fetchall()


async def mark_sent(pool: DictPool, message_id: uuid.UUID) -> None:
    """Supprime le message : la file ne conserve rien après l'envoi."""
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM outbox WHERE id = %s", (message_id,))


async def mark_failed(
    pool: DictPool, message_id: uuid.UUID, error: str, max_attempts: int
) -> bool:
    """Note l'échec. Renvoie True si le message est définitivement abandonné.

    Au-delà du nombre d'essais, la ligne est supprimée : on ne peut pas
    prévenir l'utilisateur par e-mail que son e-mail n'est pas parti, donc il
    ne reste qu'à journaliser.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            UPDATE outbox SET last_error = %s
            WHERE id = %s
            RETURNING attempts
            """,
            (error[:500], message_id),
        )
        row = await cur.fetchone()
        if row is None:
            return False
        if int(row["attempts"]) >= max_attempts:
            await conn.execute("DELETE FROM outbox WHERE id = %s", (message_id,))
            return True
    return False


async def pending_count(pool: DictPool) -> int:
    """Taille de la file, pour la sonde de supervision."""
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM outbox")
        row = await cur.fetchone()
    return int(row["n"]) if row else 0
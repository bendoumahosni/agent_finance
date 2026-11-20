"""Accès aux données des conversations.

Motif *repository* : le reste de l'application demande « les conversations de
cet utilisateur » sans savoir que c'est du SQL.

Point clé de sécurité : chaque requête filtre sur `user_id`. L'autorisation est
donc portée par la requête elle-même, pas par un contrôle en Python qu'on
pourrait oublier en ajoutant une fonction.

Les valeurs passent toujours en paramètres (%s), jamais par concaténation :
c'est ce qui empêche les injections SQL.
"""

import uuid

from psycopg.rows import DictRow

from financial_agent.db.pool import DictPool


async def create_conversation(
    pool: DictPool,
    conversation_id: uuid.UUID,
    user_id: str,
    title: str,
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES (%s, %s, %s)",
            (conversation_id, user_id, title),
        )


async def get_conversation(
    pool: DictPool, conversation_id: uuid.UUID, user_id: str
) -> DictRow | None:
    """Renvoie la conversation si elle appartient à cet utilisateur, sinon None."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations"
            " WHERE id = %s AND user_id = %s",
            (conversation_id, user_id),
        )
        return await cur.fetchone()


async def list_conversations(
    pool: DictPool, user_id: str, limit: int
) -> list[DictRow]:
    """Les conversations de l'utilisateur, la plus récemment active d'abord.

    L'index (user_id, updated_at DESC) correspond exactement à cette requête.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations"
            " WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s",
            (user_id, limit),
        )
        return await cur.fetchall()


async def touch_conversation(
    pool: DictPool, conversation_id: uuid.UUID
) -> None:
    """Met à jour `updated_at` après un échange réussi."""
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE conversations SET updated_at = now() WHERE id = %s",
            (conversation_id,),
        )


async def delete_conversation(
    pool: DictPool, conversation_id: uuid.UUID, user_id: str
) -> bool:
    """Supprime la conversation si elle appartient à l'utilisateur.

    `RETURNING id` évite une requête de vérification supplémentaire : on sait
    si une ligne a réellement été supprimée.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM conversations WHERE id = %s AND user_id = %s RETURNING id",
            (conversation_id, user_id),
        )
        return await cur.fetchone() is not None

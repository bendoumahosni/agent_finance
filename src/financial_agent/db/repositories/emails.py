"""Adresses e-mail de destination, et leur vérification.

Cette table contient délibérément une donnée personnelle, contrairement aux
messages de conversation dont les adresses sont rédigées. La différence n'est
pas technique, elle est de finalité : ici l'adresse EST le service rendu
(envoyer un résultat), elle arrive dans un champ typé d'un endpoint dédié, et
elle est consultable et supprimable par son propriétaire.

Le jeton de confirmation n'est jamais stocké : seul son hachage l'est. Une
copie de la base ne doit pas permettre de confirmer une adresse, pour la même
raison qu'on ne stocke pas un mot de passe en clair.
"""

import hashlib
import logging
import secrets
import uuid

from psycopg.rows import DictRow

from financial_agent.db.pool import DictPool

logger = logging.getLogger(__name__)

TOKEN_BYTES = 32


def new_token() -> tuple[str, str]:
    """Jeton de confirmation et son hachage.

    `secrets` et non `random` : le second est prévisible, donc un jeton
    forgeable.
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def upsert_pending(
    pool: DictPool,
    user_id: str,
    address: str,
    token_hash: str,
    ttl_seconds: int,
) -> uuid.UUID:
    """Enregistre une adresse en attente de confirmation.

    Si l'adresse existe déjà pour cet utilisateur, son jeton est remplacé et
    sa vérification annulée : redemander un lien doit invalider le précédent,
    sinon un ancien lien resterait utilisable.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO email_addresses
                (id, user_id, address, token_hash, token_expires_at)
            VALUES
                (%(id)s, %(user_id)s, lower(%(address)s), %(token_hash)s,
                 now() + make_interval(secs => %(ttl)s))
            ON CONFLICT (user_id, address) DO UPDATE SET
                token_hash = %(token_hash)s,
                token_expires_at = now() + make_interval(secs => %(ttl)s),
                verified_at = NULL
            RETURNING id
            """,
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "address": address,
                "token_hash": token_hash,
                "ttl": ttl_seconds,
            },
        )
        row = await cur.fetchone()
    assert row is not None  # RETURNING garantit une ligne
    return row["id"]


async def confirm(pool: DictPool, token_hash: str) -> bool:
    """Valide une adresse depuis le hachage de son jeton.

    Le jeton est effacé après usage : un lien ne sert qu'une fois. La
    condition sur `token_expires_at` est dans le SQL, donc impossible à
    oublier côté appelant.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            UPDATE email_addresses
            SET verified_at = now(), token_hash = NULL, token_expires_at = NULL
            WHERE token_hash = %s AND token_expires_at > now()
            RETURNING id
            """,
            (token_hash,),
        )
        return await cur.fetchone() is not None


async def list_for_user(pool: DictPool, user_id: str) -> list[DictRow]:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, address, verified_at, created_at
            FROM email_addresses
            WHERE user_id = %s
            ORDER BY created_at
            """,
            (user_id,),
        )
        return await cur.fetchall()


async def get_verified(
    pool: DictPool, email_id: uuid.UUID, user_id: str
) -> DictRow | None:
    """Adresse vérifiée appartenant à cet utilisateur, sinon None.

    Utilisée par le planificateur : une tâche ne peut viser qu'une adresse
    vérifiée et possédée. Les deux conditions sont dans la requête.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT id, address
            FROM email_addresses
            WHERE id = %s AND user_id = %s AND verified_at IS NOT NULL
            """,
            (email_id, user_id),
        )
        return await cur.fetchone()


async def delete(pool: DictPool, email_id: uuid.UUID, user_id: str) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "DELETE FROM email_addresses WHERE id = %s AND user_id = %s RETURNING id",
            (email_id, user_id),
        )
        return await cur.fetchone() is not None


async def count_for_user(pool: DictPool, user_id: str) -> int:
    """Nombre d'adresses déjà enregistrées, pour borner les abus."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM email_addresses WHERE user_id = %s",
            (user_id,),
        )
        row = await cur.fetchone()
    return int(row["n"]) if row else 0
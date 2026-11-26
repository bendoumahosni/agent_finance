"""Limitation de débit par utilisateur.

Les plafonds existants (`max_tool_calls_per_run`, `agent_timeout_seconds`)
bornent **une** requête. Celui-ci borne ce qu'un utilisateur fait dans le
temps, ce qui est le seul garde-fou contre cent requêtes légitimes d'affilée.

Le sujet est plus sensible ici que sur une API classique : chaque appel coûte
des jetons réels. L'authentification dit qui abuse, elle n'empêche rien ; le
`sub` du jeton est précisément la clé de comptage.
"""

import logging

from fastapi import HTTPException, status

from financial_agent.api.dependencies import PoolDep, SettingsDep
from financial_agent.auth.verify import CurrentPrincipal
from financial_agent.db.repositories import rate_limits as repo

logger = logging.getLogger(__name__)


async def enforce_rate_limit(
    principal: CurrentPrincipal,
    pool: PoolDep,
    settings: SettingsDep,
) -> None:
    """Dépendance posée sur le routeur : refuse au-delà du quota.

    `principal` est déjà résolu pour l'endpoint : FastAPI met en cache une
    dépendance dans une même requête, donc le jeton n'est pas vérifié deux
    fois.

    Un quota à 0 ou négatif désactive la limitation, comme l'absence de clés
    désactive le traçage : la fonctionnalité absente est une valeur neutre,
    pas une branche dans l'appelant.
    """
    if settings.rate_limit_requests <= 0:
        return

    hits, retry_after = await repo.hit(
        pool, principal.subject, settings.rate_limit_window_seconds
    )

    if hits > settings.rate_limit_requests:
        logger.warning(
            "Quota dépassé user=%s hits=%d fenêtre=%ds",
            principal.subject,
            hits,
            settings.rate_limit_window_seconds,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes. Réessayez dans quelques instants.",
            # Retry-After est la façon normalisée de dire au client quand
            # revenir : sans lui, il réessaie en boucle immédiatement.
            headers={"Retry-After": str(retry_after)},
        )
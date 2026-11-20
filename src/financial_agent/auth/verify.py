"""Vérification des jetons JWT émis par le fournisseur OIDC (Keycloak).

Principe : le fournisseur signe avec sa clé privée (RS256), l'API vérifie avec
la clé publique récupérée sur son point d'accès JWKS. L'API ne peut donc pas
émettre de jetons, et il n'y a aucun secret de signature à stocker ici.
"""

import logging
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from financial_agent.auth.principal import Principal
from financial_agent.config import get_settings

logger = logging.getLogger(__name__)

# auto_error=False : on gère nous-mêmes l'absence de jeton pour renvoyer
# toujours un 401 cohérent, avec l'en-tête WWW-Authenticate.
bearer = HTTPBearer(auto_error=False)

BearerCreds = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]


@lru_cache
def get_jwks_client() -> PyJWKClient:
    """Client JWKS unique. Les clés publiques sont mises en cache, donc une
    courte indisponibilité de Keycloak n'empêche pas de vérifier les jetons."""
    return PyJWKClient(get_settings().jwks_url)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_principal(creds: BearerCreds = None) -> Principal:
    """Dépendance FastAPI : vérifie le jeton et renvoie l'appelant.

    Volontairement synchrone (`def`) : la récupération des clés JWKS est un
    appel réseau bloquant. FastAPI exécute les dépendances synchrones dans un
    pool de threads ; dans une dépendance `async`, cet appel gèlerait la
    boucle d'événements et donc tous les utilisateurs.
    """
    if creds is None:
        raise _unauthorized("Authentification requise.")

    settings = get_settings()
    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(creds.credentials)
        payload = jwt.decode(
            creds.credentials,
            signing_key.key,
            algorithms=["RS256"],  # algorithme imposé, jamais lu dans le jeton
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        logger.info("JWT expiré")
        raise _unauthorized("Token expiré.") from exc
    except jwt.PyJWTError as exc:
        # La cause technique va dans les logs, jamais dans la réponse.
        logger.warning("JWT rejeté (%s): %s", type(exc).__name__, exc)
        raise _unauthorized("Token invalide.") from exc

    roles = frozenset(payload.get("realm_access", {}).get("roles", []))
    # Un jeton obtenu en client_credentials n'a pas de session utilisateur.
    is_service = "sid" not in payload

    return Principal(subject=str(payload["sub"]), roles=roles, is_service=is_service)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_roles(*required: str):
    """Fabrique une dépendance qui exige un ou plusieurs rôles.

    401 = « je ne sais pas qui vous êtes ».
    403 = « je sais qui vous êtes, mais vous n'avez pas le droit ».

    Usage :
        @router.get("/stats", dependencies=[Depends(require_roles("admin"))])
    """

    def checker(principal: CurrentPrincipal) -> Principal:
        if not principal.has_roles(*required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Droits insuffisants.",
            )
        return principal

    return checker

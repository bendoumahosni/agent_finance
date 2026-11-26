"""Adresses de destination et leur vérification.

Pourquoi un endpoint dédié et pas une adresse donnée en conversation : un
message de chat est rédigé (les adresses y sont retirées) et c'est voulu. Une
adresse de destination n'est pas du contenu conversationnel, c'est de la
**configuration structurée** : elle arrive dans un champ typé, validée par
Pydantic, sur un endpoint qui a cette seule finalité.

C'est la distinction à retenir : la rédaction protège ce qui transite sans
raison ; un champ typé déclare une donnée dont on a besoin.
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from financial_agent.api.dependencies import PoolDep, SettingsDep
from financial_agent.api.rate_limit import enforce_rate_limit
from financial_agent.api.schemas import EmailAddressIn, EmailAddressOut
from financial_agent.auth.verify import CurrentPrincipal
from financial_agent.db.repositories import emails as repo
from financial_agent.db.repositories import outbox as outbox_repo
from financial_agent.notify import email as mailer

logger = logging.getLogger(__name__)

# La création envoie un e-mail à une adresse choisie par l'appelant : sans
# quota, l'endpoint serait un relais d'envoi. D'où la limitation de débit,
# comme sur /chat.
router = APIRouter(
    prefix="/emails", tags=["emails"], dependencies=[Depends(enforce_rate_limit)]
)

# Second routeur, PUBLIC et sans limitation : l'utilisateur ouvre le lien
# depuis sa boîte mail, où il n'a pas de jeton JWT. C'est le jeton de
# confirmation qui authentifie l'action, et lui seul.
public_router = APIRouter(prefix="/emails", tags=["emails"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def request_address(
    body: EmailAddressIn,
    principal: CurrentPrincipal,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict[str, str]:
    """Enregistre une adresse et envoie un lien de confirmation.

    Réponse 202 et non 201 : l'adresse existe mais n'est pas utilisable tant
    qu'elle n'est pas confirmée.
    """
    if not mailer.is_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="L'envoi d'e-mails n'est pas configuré sur ce serveur.",
        )

    if await repo.count_for_user(pool, principal.subject) >= (
        settings.max_email_addresses_per_user
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nombre maximal d'adresses atteint. Supprimez-en une d'abord.",
        )

    token, token_hash = repo.new_token()
    await repo.upsert_pending(
        pool,
        principal.subject,
        str(body.address),
        token_hash,
        settings.email_token_ttl_seconds,
    )

    link = f"{settings.public_base_url}/emails/confirm?token={token}"

    # Mise en file plutôt qu'envoi direct : un fournisseur lent ou
    # indisponible rendrait cet endpoint lent puis le ferait échouer. La
    # boucle de fond envoie et réessaie avec un délai croissant.
    await outbox_repo.enqueue(
        pool,
        str(body.address),
        "Confirmez votre adresse pour l'agent financier",
        mailer.confirmation_body(str(body.address), link),
    )

    logger.info("Confirmation mise en file user=%s", principal.subject)
    return {"detail": "Un lien de confirmation vous a été envoyé."}


@router.get("", response_model=list[EmailAddressOut])
async def list_addresses(
    principal: CurrentPrincipal,
    pool: PoolDep,
) -> list[dict]:
    """Adresses enregistrées par l'appelant, vérifiées ou non."""
    return await repo.list_for_user(pool, principal.subject)


@router.delete("/{email_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_address(
    email_id: uuid.UUID,
    principal: CurrentPrincipal,
    pool: PoolDep,
) -> Response:
    """Supprime une adresse. Obligatoire : c'est une donnée personnelle."""
    if not await repo.delete(pool, email_id, principal.subject):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Adresse introuvable."
        )
    logger.info("Adresse supprimée user=%s", principal.subject)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@public_router.get("/confirm")
async def confirm_address(
    pool: PoolDep,
    token: str = Query(..., min_length=16, max_length=256),
) -> dict[str, str]:
    """Valide une adresse depuis le lien reçu par e-mail.

    Message identique en cas d'échec, qu'il s'agisse d'un jeton inconnu ou
    expiré : distinguer les deux renseignerait un attaquant sur l'existence
    d'un jeton.
    """
    if not await repo.confirm(pool, repo.hash_token(token)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lien invalide ou expiré. Demandez-en un nouveau.",
        )
    return {"detail": "Adresse confirmée. Vous pouvez programmer des envois."}
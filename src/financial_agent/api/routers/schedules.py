"""Tâches planifiées : création, consultation, réactivation, suppression.

Le prompt est contrôlé et rédigé à la création comme n'importe quel message :
c'est une instruction qui sera rejouée chaque jour, donc un vecteur
d'injection persistant si on ne la filtrait pas une seule fois à l'entrée.

L'adresse de destination est désignée par son identifiant, et doit être
vérifiée. Le modèle n'intervient jamais dans ce choix : il ne peut donc pas
rediriger un envoi.
"""

import logging
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response, status

from financial_agent.agent.guardrails import (
    PaymentDataRejected,
    check_user_input,
    sanitize_user_input,
)
from financial_agent.api.dependencies import PoolDep, SettingsDep
from financial_agent.api.rate_limit import enforce_rate_limit
from financial_agent.api.schemas import ScheduleIn, ScheduleOut
from financial_agent.auth.verify import CurrentPrincipal
from financial_agent.db.repositories import emails as emails_repo
from financial_agent.db.repositories import schedules as repo

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/schedules",
    tags=["schedules"],
    dependencies=[Depends(enforce_rate_limit)],
)


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: ScheduleIn,
    principal: CurrentPrincipal,
    pool: PoolDep,
    settings: SettingsDep,
) -> dict:
    """Programme une exécution quotidienne à une heure locale."""
    user_id = principal.subject

    # Le fuseau est validé ici : une valeur inconnue ferait échouer le
    # calcul d'échéance en base, bien plus tard et sans message clair.
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Fuseau horaire inconnu : {body.timezone}",
        ) from exc

    try:
        check_user_input(body.prompt)
    except PaymentDataRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Votre consigne semble contenir des données de paiement.",
        ) from exc

    prompt, redacted = sanitize_user_input(body.prompt)
    if redacted:
        logger.info("Rédaction appliquée à une tâche user=%s", user_id)

    # L'adresse doit être vérifiée et possédée : sans cela, on aurait un
    # relais d'envoi vers n'importe qui.
    if await emails_repo.get_verified(pool, body.email_id, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Adresse inconnue ou non confirmée.",
        )

    if await repo.count_for_user(pool, user_id) >= (
        settings.max_scheduled_tasks_per_user
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nombre maximal de tâches atteint. Supprimez-en une d'abord.",
        )

    task = await repo.create(
        pool,
        user_id,
        prompt,
        body.email_id,
        body.run_at_time.isoformat(),
        body.timezone,
    )
    logger.info("Tâche planifiée créée user=%s id=%s", user_id, task["id"])
    return task


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    principal: CurrentPrincipal,
    pool: PoolDep,
) -> list[dict]:
    """Tâches de l'appelant, avec leur état et la cause du dernier échec."""
    return await repo.list_for_user(pool, principal.subject)


@router.post("/{task_id}/enable", response_model=ScheduleOut)
async def enable_schedule(
    task_id: uuid.UUID,
    principal: CurrentPrincipal,
    pool: PoolDep,
) -> dict:
    """Réactive une tâche désactivée après un échec.

    Contrepartie obligatoire du choix « désactiver au premier échec » : sans
    cet endpoint, une tâche arrêtée serait définitivement perdue.
    """
    if not await repo.enable(pool, task_id, principal.subject):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tâche introuvable."
        )

    tasks = await repo.list_for_user(pool, principal.subject)
    return next(task for task in tasks if task["id"] == task_id)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    task_id: uuid.UUID,
    principal: CurrentPrincipal,
    pool: PoolDep,
) -> Response:
    if not await repo.delete(pool, task_id, principal.subject):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tâche introuvable."
        )
    logger.info("Tâche supprimée user=%s id=%s", principal.subject, task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
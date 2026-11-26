"""Boucles de fond : envoi des e-mails et exécution des tâches planifiées.

Deux boucles, même forme que la reprise MCP : une fonction testable sans
minuteur, et une boucle qui l'appelle et protège son corps. Une exception non
attrapée tuerait la tâche silencieusement, et plus rien ne s'exécuterait.

Rien ici n'est exposé au modèle. L'agent produit du texte, ces boucles le
délivrent : aucune injection de prompt ne peut provoquer un envoi.
"""

import asyncio
import logging
import uuid

from fastapi import FastAPI

from financial_agent.agent.runner import run_financial_agent
from financial_agent.config import Settings
from financial_agent.db.repositories import conversations as conversations_repo
from financial_agent.db.repositories import emails as emails_repo
from financial_agent.db.repositories import outbox as outbox_repo
from financial_agent.db.repositories import schedules as schedules_repo
from financial_agent.notify import email as mailer

logger = logging.getLogger(__name__)

TITLE_MAX_LENGTH = 60

RESULT_SUBJECT = "Votre rapport de l'agent financier"

FAILURE_SUBJECT = "Votre tâche planifiée a été désactivée"
FAILURE_BODY = (
    "Bonjour,\n\n"
    "Votre tâche planifiée n'a pas pu être exécutée et a été désactivée :\n"
    "{reason}\n\n"
    "Elle ne se relancera pas d'elle-même. Vous pouvez la réactiver depuis "
    "l'API, ou la supprimer si elle ne vous sert plus.\n"
)


# --- File de sortie ---------------------------------------------------------


async def drain_outbox(app: FastAPI, settings: Settings) -> int:
    """Envoie les messages dus. Renvoie le nombre d'envois réussis."""
    pool = app.state.pool

    if not mailer.is_configured(settings):
        return 0

    messages = await outbox_repo.claim_due(
        pool, settings.outbox_batch_size, settings.outbox_backoff_seconds
    )
    sent = 0

    for message in messages:
        try:
            await mailer.send(
                settings,
                to=message["to_address"],
                subject=message["subject"],
                body=message["body"],
            )
        except mailer.EmailSendFailed as exc:
            abandoned = await outbox_repo.mark_failed(
                pool,
                message["id"],
                type(exc.__cause__).__name__,
                settings.outbox_max_attempts,
            )
            if abandoned:
                # On ne peut pas prévenir par e-mail qu'un e-mail ne part
                # pas : il ne reste que le journal.
                logger.error(
                    "Message abandonné après %d essais (id=%s)",
                    message["attempts"],
                    message["id"],
                )
            continue

        await outbox_repo.mark_sent(pool, message["id"])
        sent += 1

    if sent:
        logger.info("%d message(s) envoyé(s)", sent)
    return sent


async def outbox_loop(app: FastAPI, settings: Settings) -> None:
    while True:
        await asyncio.sleep(settings.outbox_poll_seconds)
        try:
            await drain_outbox(app, settings)
        except Exception:
            logger.exception("Échec de la boucle d'envoi")


# --- Tâches planifiées ------------------------------------------------------


async def _execute_task(app: FastAPI, settings: Settings, task: dict) -> None:
    """Exécute une tâche et met son résultat en file.

    En cas d'échec, la tâche est désactivée et l'utilisateur prévenu : une
    tâche qui échoue en silence pendant des semaines est pire qu'une tâche
    arrêtée dont on est averti.
    """
    pool = app.state.pool
    task_id = task["id"]
    user_id = task["user_id"]

    # L'adresse doit être vérifiée ET appartenir à l'utilisateur. Les deux
    # conditions sont dans la requête, donc impossibles à oublier.
    address = await emails_repo.get_verified(pool, task["email_id"], user_id)
    if address is None:
        logger.warning("Tâche %s : adresse non vérifiée, désactivation", task_id)
        await schedules_repo.disable_with_error(
            pool, task_id, "Adresse de destination introuvable ou non vérifiée."
        )
        return

    # Une vraie conversation : l'utilisateur la voit dans /conversations, peut
    # la relire et la supprimer. Un thread synthétique laisserait des
    # checkpoints orphelins que personne ne pourrait effacer.
    conversation_id = uuid.uuid4()
    await conversations_repo.create_conversation(
        pool, conversation_id, user_id, task["prompt"][:TITLE_MAX_LENGTH]
    )

    try:
        reply = await asyncio.wait_for(
            run_financial_agent(
                app.state.agent, task["prompt"], str(conversation_id), user_id
            ),
            timeout=settings.agent_timeout_seconds,
        )
    except Exception as exc:
        logger.exception("Tâche %s en échec, désactivation", task_id)
        reason = f"{type(exc).__name__}"
        await schedules_repo.disable_with_error(pool, task_id, reason)
        await outbox_repo.enqueue(
            pool,
            address["address"],
            FAILURE_SUBJECT,
            FAILURE_BODY.format(reason="L'exécution a échoué."),
        )
        return

    await outbox_repo.enqueue(pool, address["address"], RESULT_SUBJECT, reply.text)
    await schedules_repo.clear_error(pool, task_id)
    logger.info("Tâche %s exécutée, résultat mis en file", task_id)


async def run_due_tasks(app: FastAPI, settings: Settings) -> int:
    """Prend les tâches dues et les exécute. Renvoie le nombre traité."""
    tasks = await schedules_repo.claim_due(
        app.state.pool, settings.scheduler_batch_size
    )
    for task in tasks:
        await _execute_task(app, settings, dict(task))
    return len(tasks)


async def scheduler_loop(app: FastAPI, settings: Settings) -> None:
    while True:
        await asyncio.sleep(settings.scheduler_poll_seconds)
        try:
            await run_due_tasks(app, settings)
        except Exception:
            logger.exception("Échec de la boucle d'envoi")
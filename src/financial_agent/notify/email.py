"""Envoi d'e-mails.

Deux garde-fous de conception :

1. **Aucun outil d'envoi n'est exposé au modèle.** L'agent produit du texte,
   la plateforme l'envoie. Une injection de prompt ne peut donc pas provoquer
   un e-mail, et c'est la raison pour laquelle cette fonction ne vit pas dans
   `agent/tools/`.
2. **Le contenu de l'e-mail de confirmation n'est pas personnalisable.** Sinon
   l'endpoint de création d'adresse deviendrait un relais : on enverrait un
   texte choisi par un tiers à une adresse choisie par lui.

Sans configuration SMTP, l'envoi est désactivé et l'appelant en est informé :
même principe que pour MCP et Langfuse, l'absence est une valeur neutre.
"""

import logging
from email.message import EmailMessage

import aiosmtplib

from financial_agent.config import Settings

logger = logging.getLogger(__name__)


class EmailNotConfigured(Exception):
    """Aucun serveur SMTP configuré : l'envoi est impossible."""


class EmailSendFailed(Exception):
    """Le serveur SMTP a refusé ou n'a pas répondu."""


def is_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


async def send(settings: Settings, to: str, subject: str, body: str) -> None:
    """Envoie un message en texte brut.

    Texte brut et non HTML : un agent produit du texte, et le HTML ouvrirait
    la question de l'échappement de contenu venant du web.
    """
    if not is_configured(settings):
        raise EmailNotConfigured

    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user or None,
            password=settings.smtp_password or None,
            start_tls=settings.smtp_starttls,
            timeout=settings.smtp_timeout_seconds,
        )
    except Exception as exc:
        # L'adresse ne doit jamais apparaître dans les logs : c'est une
        # donnée personnelle, et les logs partent souvent ailleurs.
        logger.warning("Envoi SMTP échoué (%s)", type(exc).__name__)
        raise EmailSendFailed from exc


def confirmation_body(address: str, link: str) -> str:
    """Corps du message de confirmation. Volontairement figé."""
    return (
        "Bonjour,\n\n"
        "Vous avez demandé à recevoir les résultats de l'agent financier à "
        f"cette adresse ({address}).\n\n"
        "Pour confirmer, ouvrez ce lien :\n"
        f"{link}\n\n"
        "Ce lien expire sous 24 heures et ne sert qu'une fois.\n"
        "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message : "
        "aucune donnée ne vous sera envoyée.\n"
    )
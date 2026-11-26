"""Modèles de requête et de réponse de l'API.

Valider au bord du système permet de ne plus se méfier à l'intérieur : une
donnée mal formée est rejetée avec un 422 avant d'atteindre la logique.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field
from datetime import datetime, time

class MemoryOut(BaseModel):
    """Un souvenir de la mémoire longue."""

    key: str
    value: str
    updated_at: datetime | None = None

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)
    # Le type UUID fait valider le format par Pydantic : une valeur arbitraire
    # ne peut pas se retrouver dans le thread_id.
    conversation_id: uuid.UUID | None = Field(
        None,
        description="Omettre pour démarrer une nouvelle conversation.",
    )

class EmailAddressIn(BaseModel):
    """Adresse de destination.

    `EmailStr` valide le format au bord : une adresse mal formée est rejetée
    par un 422 avant d'atteindre la base ou le serveur SMTP.
    """

    address: EmailStr


class EmailAddressOut(BaseModel):
    id: uuid.UUID
    address: str
    verified_at: datetime | None = None
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

class ScheduleIn(BaseModel):
    """Création d'une tâche quotidienne.

    `run_at_time` est une heure locale et `timezone` son fuseau : stocker une
    heure sans fuseau décalerait l'exécution d'une heure deux fois par an.
    """

    prompt: str = Field(..., min_length=1, max_length=2000)
    email_id: uuid.UUID
    run_at_time: time
    timezone: str = Field("Europe/Paris", max_length=64)


class ScheduleOut(BaseModel):
    id: uuid.UUID
    prompt: str
    run_at_time: time
    timezone: str
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime


class HealthResponse(BaseModel):
    status: str

class MessageOut(BaseModel):
    """Un message de l'historique, aplati pour le client.

    `role` suit la nomenclature LangChain : human, ai, tool, system.
    `tool_calls` permet au client d'afficher « a utilisé la calculatrice ».
    """

    role: str
    content: str
    tool_calls: list[str] | None = Field(
        None, description="Noms des outils appelés par ce message."
    )

class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    response: str
    # Capacités momentanément indisponibles (serveur MCP injoignable).
    # Valeur par défaut vide : les clients existants ne cassent pas.
    warnings: list[str] = Field(default_factory=list)
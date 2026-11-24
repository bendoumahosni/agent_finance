"""Mémoire longue : ce que l'agent retient d'une conversation à l'autre.

À ne pas confondre avec le checkpointer :

| Couche          | Portée                    | Contenu                        |
|-----------------|---------------------------|--------------------------------|
| Checkpointer    | une conversation          | les messages échangés          |
| Store (ici)     | toutes les conversations  | un petit profil de travail     |

Décision de périmètre, et elle est importante : on garde le **contexte de
travail** (métier, sujets suivis, devise de référence, langue), pas les
**coordonnées**. Stocker durablement un e-mail ou un téléphone serait
contradictoire avec la rédaction appliquée à l'entrée de l'API.

Le Store est donc un profil assumé et déclaré, soumis à trois obligations :
l'utilisateur doit pouvoir le consulter, le corriger et le supprimer. C'est
ce que fournissent les endpoints /memories.

Rien ne s'écrit automatiquement : le modèle appelle `save_memory` quand il
juge une information durable. On voit donc la décision dans l'historique de
la conversation, ce qui est auditable.
"""

import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import tool
from langgraph.config import get_config, get_store
from langgraph.store.base import BaseStore

from financial_agent.agent.guardrails import sanitize_user_input

logger = logging.getLogger(__name__)

NAMESPACE_ROOT = "memories"
MAX_VALUE_LENGTH = 200
MAX_MEMORIES_PER_USER = 20
KEY_PATTERN = re.compile(r"^[a-z0-9_]{2,40}$")


def namespace_for(user_id: str) -> tuple[str, str]:
    """Espace de noms d'un utilisateur.

    Point de sécurité central : le namespace est TOUJOURS dérivé de
    l'identité authentifiée, jamais d'un argument fourni par le modèle. Si
    `user_id` était un paramètre d'outil, une injection de prompt pourrait
    faire lire ou écrire la mémoire d'un autre utilisateur.
    """
    return (NAMESPACE_ROOT, user_id)


def _current_user_id() -> str | None:
    """Identité de l'appelant, lue dans la configuration d'exécution."""
    configurable = (get_config() or {}).get("configurable", {})
    user_id = configurable.get("user_id")
    return str(user_id) if user_id else None


def normalize_key(key: str) -> str | None:
    """Normalise une clé proposée par le modèle, ou None si invalide.

    Le modèle est créatif sur les noms ; on impose un format stable pour que
    les souvenirs restent listables et remplaçables.
    """
    candidate = re.sub(r"[\s-]+", "_", key.strip().lower())
    candidate = re.sub(r"[^a-z0-9_]", "", candidate)
    return candidate if KEY_PATTERN.match(candidate) else None


@tool
async def save_memory(key: str, value: str) -> str:
    """Mémorise une information durable sur l'utilisateur, réutilisable dans
    les prochaines conversations.

    À utiliser pour le contexte de travail : métier, secteur, sujets suivis,
    devise de référence, langue préférée. Exemples de clés : `metier`,
    `devise_reference`, `sujets_suivis`.

    Ne jamais mémoriser de coordonnées (e-mail, téléphone, adresse) ni de
    données bancaires.
    """
    user_id = _current_user_id()
    if user_id is None:
        logger.warning("save_memory appelé sans user_id dans la configuration")
        return "Mémorisation impossible : utilisateur inconnu."

    normalized = normalize_key(key)
    if normalized is None:
        return (
            "Clé invalide. Utilisez un nom court en minuscules, "
            "par exemple 'metier' ou 'devise_reference'."
        )

    value = value.strip()
    if not value:
        return "Valeur vide : rien à mémoriser."
    if len(value) > MAX_VALUE_LENGTH:
        return f"Valeur trop longue (maximum {MAX_VALUE_LENGTH} caractères)."

    # Deuxième barrière : la rédaction a déjà retiré les coordonnées des
    # messages, mais une donnée reformulée par le modèle passerait ici.
    _, pii_labels = sanitize_user_input(value)
    if pii_labels:
        logger.warning(
            "save_memory refusé (données personnelles) user=%s clé=%s",
            user_id,
            normalized,
        )
        return (
            "Refusé : cette information contient des données personnelles "
            f"({', '.join(pii_labels)}). Elles ne sont pas conservées."
        )

    store = get_store()
    namespace = namespace_for(user_id)

    existing = await store.asearch(namespace, limit=MAX_MEMORIES_PER_USER + 1)
    known_keys = {item.key for item in existing}
    if normalized not in known_keys and len(known_keys) >= MAX_MEMORIES_PER_USER:
        return (
            f"Limite atteinte ({MAX_MEMORIES_PER_USER} souvenirs). "
            "Demandez à l'utilisateur lequel supprimer."
        )

    await store.aput(namespace, normalized, {"value": value})
    logger.info("Souvenir enregistré user=%s clé=%s", user_id, normalized)
    return f"Mémorisé : {normalized} = {value}"


@tool
async def forget_memory(key: str) -> str:
    """Oublie une information mémorisée sur l'utilisateur.

    À utiliser quand l'utilisateur demande d'effacer ou de corriger quelque
    chose que l'agent avait retenu.
    """
    user_id = _current_user_id()
    if user_id is None:
        return "Suppression impossible : utilisateur inconnu."

    normalized = normalize_key(key)
    if normalized is None:
        return "Clé invalide."

    store = get_store()
    await store.adelete(namespace_for(user_id), normalized)
    logger.info("Souvenir supprimé user=%s clé=%s", user_id, normalized)
    return f"Oublié : {normalized}"


async def list_memories(store: BaseStore, user_id: str) -> list[dict[str, Any]]:
    """Souvenirs d'un utilisateur, pour les endpoints /memories.

    Prend le store en paramètre plutôt que `get_store()` : hors exécution du
    graphe, il n'y a pas de contexte LangGraph.
    """
    items = await store.asearch(namespace_for(user_id), limit=MAX_MEMORIES_PER_USER)
    return [
        {
            "key": item.key,
            "value": item.value.get("value", ""),
            "updated_at": item.updated_at,
        }
        for item in sorted(items, key=lambda i: i.key)
    ]


async def delete_memory(store: BaseStore, user_id: str, key: str) -> None:
    await store.adelete(namespace_for(user_id), key)


async def delete_all_memories(store: BaseStore, user_id: str) -> int:
    """Efface tout le profil d'un utilisateur. Renvoie le nombre supprimé."""
    namespace = namespace_for(user_id)
    items = await store.asearch(namespace, limit=MAX_MEMORIES_PER_USER)
    for item in items:
        await store.adelete(namespace, item.key)
    return len(items)


MEMORY_PROMPT_HEADER = (
    "\n\n[PROFIL DE L'UTILISATEUR — mémorisé lors de conversations précédentes]\n"
)


class MemoryInjectionMiddleware(AgentMiddleware):
    """Injecte le profil de l'utilisateur dans le prompt système.

    Lecture automatique, écriture explicite : l'agent « sait » déjà, sans
    avoir à appeler un outil pour se souvenir. Un outil de lecture coûterait
    un aller-retour de modèle à chaque conversation.

    Le profil est ajouté au prompt système, et non à l'historique des
    messages : il ne doit pas être enregistré dans les checkpoints de la
    conversation, sinon il y serait figé et dupliqué.
    """

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        store = getattr(request.runtime, "store", None)
        user_id = _current_user_id()

        if store is None or user_id is None:
            return await handler(request)

        try:
            memories = await list_memories(store, user_id)
        except Exception:
            # Une mémoire indisponible ne doit pas empêcher de répondre.
            logger.exception("Lecture de la mémoire longue impossible")
            return await handler(request)

        if memories:
            profile = "\n".join(f"- {m['key']} : {m['value']}" for m in memories)
            request = request.override(
                system_prompt=request.system_prompt + MEMORY_PROMPT_HEADER + profile
            )

        return await handler(request)
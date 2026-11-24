"""Traçage des exécutions de l'agent avec Langfuse.

Les logs disent ce qui s'est passé dans l'API ; ils ne disent pas pourquoi
l'agent a appelé tel outil, combien de jetons l'échange a coûté, ni où est
passée la latence. C'est ce que donne une trace : un arbre d'appels
(modèle, outils, middlewares) avec durées, entrées, sorties et coûts.

Comme pour MCP, la fonctionnalité est **optionnelle et dégradable** : sans
clés configurées, l'application fonctionne exactement comme avant, sans
traçage.

Les traces partent chez un tiers : tout ce qu'elles contiennent sort de votre
infrastructure. D'où la fonction de masquage ci-dessous, dernière barrière
après la rédaction appliquée à l'entrée de l'API.
"""

import logging
from typing import Any

from langfuse import Langfuse
from langfuse.types import MaskOtelSpansParams, MaskOtelSpansResult, OtelSpanPatch

from financial_agent.agent.guardrails import sanitize_user_input
from financial_agent.config import Settings

logger = logging.getLogger(__name__)

# Client unique, créé au démarrage. None = traçage désactivé.
_client: Langfuse | None = None

def current_trace_url() -> str | None:
    """Lien vers la trace en cours, à mettre dans les logs d'erreur."""
    if _client is None:
        return None
    try:
        return _client.get_trace_url(trace_id=_client.get_current_trace_id())
    except Exception:
        return None

def mask_pii(*, data: Any, **_kwargs: Any) -> Any:
    """Dernière barrière avant que les données ne quittent l'infrastructure.

    La rédaction d'entrée couvre déjà les messages de l'utilisateur, mais une
    trace contient aussi le prompt système (donc le profil mémorisé) et les
    résultats d'outils, qui viennent du web ou de serveurs MCP.
    """
    if isinstance(data, str):
        cleaned, _ = sanitize_user_input(data)
        return cleaned
    if isinstance(data, dict):
        return {key: mask_pii(data=value) for key, value in data.items()}
    if isinstance(data, list):
        return [mask_pii(data=item) for item in data]
    return data


# Attributs de span qui portent du texte issu de l'utilisateur, du modèle
# ou des outils. Ce sont ceux à nettoyer.
TEXT_ATTRIBUTES = (
    "langfuse.observation.input",
    "langfuse.observation.output",
    "langfuse.trace.input",
    "langfuse.trace.output",
)


def mask_otel_spans(*, params: MaskOtelSpansParams) -> MaskOtelSpansResult:
    """Masque les attributs bruts des spans, à l'export.

    Pourquoi ce second masquage : `mask` ne couvre que les champs que le SDK
    écrit lui-même. L'intégration LangChain pose directement
    `langfuse.observation.output` sur le span OpenTelemetry, qui échappe donc
    au premier filtre. Vérifié : sans cette fonction, une adresse e-mail
    produite par le modèle part en clair.
    """
    patches: dict[Any, Any] = {}

    for identifier, span in params.spans.items():
        changed: dict[str, Any] = {}
        for name in TEXT_ATTRIBUTES:
            value = span.attributes.get(name)
            if isinstance(value, str):
                cleaned, labels = sanitize_user_input(value)
                if labels:
                    changed[name] = cleaned
        if changed:
            patches[identifier] = OtelSpanPatch(
                set_attributes=changed, delete_attributes=()
            )

    return MaskOtelSpansResult(span_patches=patches)


def init_tracing(settings: Settings) -> Langfuse | None:
    """Initialise le client Langfuse. Renvoie None si le traçage est désactivé.

    Appelé une fois au démarrage, depuis le `lifespan`.
    """
    global _client

    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.info("Langfuse non configuré : traçage désactivé.")
        _client = None
        return None

    _client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
        environment=settings.langfuse_environment,
        release=settings.langfuse_release,
        # Échantillonnage : en forte charge, tracer 100 % des requêtes coûte
        # cher en réseau et en stockage.
        sample_rate=settings.langfuse_sample_rate,
        # Deux masquages complémentaires : `mask` pour ce que le SDK écrit,
        # `mask_otel_spans` pour les attributs posés directement par
        # l'intégration LangChain.
        mask=mask_pii,
        mask_otel_spans=mask_otel_spans,
    )

    # Vérification non bloquante : un Langfuse injoignable ne doit pas
    # empêcher l'API de démarrer, c'est de l'observabilité, pas du métier.
    try:
        if _client.auth_check():
            logger.info("Langfuse prêt (%s).", settings.langfuse_host)
        else:
            logger.warning("Langfuse : authentification refusée, traces perdues.")
    except Exception as exc:
        logger.warning("Langfuse injoignable au démarrage (%s).", exc)

    return _client


def shutdown_tracing() -> None:
    """Vide la file d'envoi à l'arrêt.

    Langfuse regroupe les traces par lots : sans ce vidage, les dernières
    secondes d'activité seraient perdues à chaque redéploiement.
    """
    global _client
    if _client is None:
        return
    try:
        _client.shutdown()
        logger.info("Traces Langfuse envoyées.")
    except Exception:
        logger.exception("Échec de l'envoi des traces Langfuse")
    finally:
        _client = None


def trace_config(thread_id: str, user_id: str) -> dict[str, Any]:
    """Fragment de configuration à fusionner dans l'appel de l'agent.

    Renvoie un dictionnaire vide quand le traçage est désactivé, ce qui rend
    l'appelant indifférent à la présence de Langfuse.

    `session_id` vaut l'identifiant de conversation : l'interface Langfuse
    regroupe alors tous les échanges d'une même conversation, ce qui est la
    façon naturelle de relire un incident.
    """
    if _client is None:
        return {}

    # Import tardif : le paquet n'est nécessaire que si le traçage est actif.
    from langfuse.langchain import CallbackHandler

    return {
        "callbacks": [CallbackHandler()],
        "metadata": {
            "langfuse_user_id": user_id,
            "langfuse_session_id": thread_id,
            "langfuse_trace_name": "financial-agent-chat",
        },
    }
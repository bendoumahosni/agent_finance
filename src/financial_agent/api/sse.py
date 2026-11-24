
"""Formatage Server-Sent Events.

Le protocole est volontairement minimal : des lignes `event:` et `data:`
séparées par une ligne vide. Le navigateur le consomme avec `EventSource`,
sans bibliothèque.

Pourquoi SSE et pas WebSocket : le flux est unidirectionnel (le serveur
parle, le client écoute), SSE passe les proxies HTTP sans configuration, et
le navigateur gère la reconnexion. WebSocket serait justifié s'il fallait
interrompre une génération en cours.
"""

import json
from typing import Any

# Empêche les intermédiaires de tamponner la réponse : sans cela, nginx
# accumule les octets et le flux arrive d'un bloc, ce qui annule l'intérêt.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def format_event(event: str, data: dict[str, Any]) -> str:
    """Un événement SSE.

    `ensure_ascii=False` garde les accents lisibles ; `separators` évite les
    espaces inutiles. Le JSON ne doit contenir aucun retour à la ligne, qui
    casserait le découpage du protocole — `json.dumps` s'en charge.
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"
"""Serveur MCP exposant la conversion de devises.

Service autonome, déployé à part de l'API : il a ses propres dépendances,
son propre cycle de vie et son propre mode de panne. C'est ce qui justifie
MCP ici, alors qu'une addition ne le justifierait pas.

Transport `streamable-http` : le serveur écoute sur le réseau et l'API le
joint par son nom de service Docker. Avec stdio, il faudrait l'installer
dans le conteneur de l'API, ce qui annulerait l'isolation.
"""

import logging
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("currency-mcp")

EXCHANGE_URL = os.environ.get("EXCHANGE_URL", "https://api.exchangerate.fun/latest")
TIMEOUT_SECONDS = float(os.environ.get("EXCHANGE_TIMEOUT", "10"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8001"))

# SDK mcp 2.x : la classe s'appelle MCPServer (anciennement FastMCP en 1.x),
# et l'hôte et le port se passent à run(), non au constructeur.
mcp = MCPServer(
    "currency",
    instructions="Conversion de montants entre devises, aux taux du jour.",
)


async def fetch_rates(base: str) -> dict[str, Any] | None:
    """Appelle l'API de taux. Renvoie None en cas d'indisponibilité."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.get(EXCHANGE_URL, params={"base": base})
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as exc:
        logger.warning("Taux de change indisponibles (%s)", exc)
        return None


def build_result(
    amount: float, from_currency: str, to_currency: str, data: dict[str, Any]
) -> dict[str, Any]:
    """Transforme la réponse de l'API en résultat exploitable (fonction pure)."""
    currency = to_currency.upper()
    rates = data.get("rates", {})

    if currency not in rates:
        return {"error": f"Devise non disponible : {currency}"}

    rate = rates[currency]
    return {
        "amount": amount,
        "from": from_currency.upper(),
        "to": currency,
        "exchange_rate": rate,
        "converted_amount": round(amount * rate, 2),
        "rate_date": data.get("date"),
        "source": "ExchangeRate.fun",
    }


@mcp.tool()
async def convert_currency(
    amount: float, from_currency: str, to_currency: str
) -> dict[str, Any]:
    """Convertit un montant entre deux devises (codes ISO 4217, ex. TND, USD, EUR).

    Exemple : 100 EUR vers USD.
    """
    data = await fetch_rates(from_currency.upper())
    if data is None:
        # On renvoie une erreur exploitable, jamais une exception : le client
        # MCP la transmettrait au modèle sous une forme inutilisable.
        return {"error": "Service de taux de change indisponible."}
    return build_result(amount, from_currency, to_currency, data)


if __name__ == "__main__":
    logger.info("Serveur MCP 'currency' démarre sur %s:%s", HOST, PORT)
    mcp.run(transport="streamable-http", host=HOST, port=PORT)
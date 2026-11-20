"""Sondes de santé — publiques : Kubernetes n'a pas de jeton.

Deux sondes distinctes, car les conséquences diffèrent :
  - /livez  : le processus répond-il ? Si non, Kubernetes redémarre le pod.
  - /readyz : peut-il servir du trafic (base joignable) ? Si non, le pod est
              retiré du service sans être redémarré.
Une sonde qui répond « ok » alors que la base est tombée est trompeuse.
"""

from fastapi import APIRouter, HTTPException, status

from financial_agent.api.dependencies import PoolDep
from financial_agent.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/")
async def read_root() -> dict[str, str]:
    return {"message": "Bienvenue sur l'API de l'agent financier."}


@router.get("/livez", response_model=HealthResponse)
async def livez() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=HealthResponse)
async def readyz(pool: PoolDep) -> HealthResponse:
    try:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Base de données injoignable.",
        ) from exc
    return HealthResponse(status="ok")


# Conservé pour compatibilité avec la version précédente de l'API.
@router.get("/health_check", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok")

"""Fixtures partagées par les tests.

Les variables d'environnement sont posées avant tout import du paquet, pour
que `get_settings()` puisse se construire sans vrai fichier .env.
"""

import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("TAVILY_API_KEY", "test-tavily-key")
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@localhost:5432/test"
)
os.environ.setdefault("OIDC_ISSUER", "https://auth.test/realms/test")
os.environ.setdefault("OIDC_AUDIENCE", "financial-agent")


@pytest.fixture
def settings():
    from financial_agent.config import Settings

    return Settings(
        openai_api_key="test-openai-key",
        tavily_api_key="test-tavily-key",
        database_url="postgresql://test:test@localhost:5432/test",
        oidc_issuer="https://auth.test/realms/test",
        oidc_audience="financial-agent",
    )

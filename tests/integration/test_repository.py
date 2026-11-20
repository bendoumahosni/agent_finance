"""Tests du repository contre une vraie base Postgres.

Ignorés si TEST_DATABASE_URL n'est pas défini, pour que `pytest` reste
exécutable sans base. En CI, on fournit un service Postgres.

    docker compose exec api pytest tests/integration/test_repository.py
"""

import os
import uuid

import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get(
    "DATABASE_URL_FOR_TESTS"
)

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL non défini : test d'intégration ignoré.",
)


@pytest.mark.asyncio
async def test_cycle_de_vie_d_une_conversation():
    from financial_agent.config import Settings
    from financial_agent.db.pool import create_pool
    from financial_agent.db.repositories import conversations as repo

    settings = Settings(
        openai_api_key="x",
        tavily_api_key="x",
        database_url=TEST_DATABASE_URL,
        oidc_issuer="https://auth.test/realms/test",
        oidc_audience="financial-agent",
    )

    async with create_pool(settings) as pool:
        conversation_id = uuid.uuid4()
        user = f"user-{uuid.uuid4()}"

        await repo.create_conversation(pool, conversation_id, user, "titre")

        found = await repo.get_conversation(pool, conversation_id, user)
        assert found is not None
        assert found["title"] == "titre"

        # Un autre utilisateur ne doit rien voir.
        assert await repo.get_conversation(pool, conversation_id, "autre") is None

        listed = await repo.list_conversations(pool, user, 10)
        assert [c["id"] for c in listed] == [conversation_id]

        assert await repo.delete_conversation(pool, conversation_id, user) is True
        assert await repo.delete_conversation(pool, conversation_id, user) is False

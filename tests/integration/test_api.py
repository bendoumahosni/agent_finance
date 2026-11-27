"""Tests de l'API sans base de données ni LLM.

On crée l'application sans son cycle de vie (`lifespan_handler=None`), puis on
remplace ses dépendances : c'est précisément ce que permet l'injection de
dépendances de FastAPI, et la raison pour laquelle les endpoints ne vont pas
chercher le pool eux-mêmes.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from financial_agent.api.dependencies import (
    get_agent,
    get_checkpointer,
    get_mcp_failures,
    get_pool,
    get_store,
)
from financial_agent.api.rate_limit import enforce_rate_limit
from financial_agent.auth.principal import Principal
from financial_agent.auth.verify import get_principal
from financial_agent.main import create_app

USER = "user-123"
OTHER_USER = "user-456"


class FakeAgent:
    async def ainvoke(self, payload, config=None):
        class Msg:
            content = "Réponse simulée de l'agent."

        return {"messages": [Msg()]}


class FakeCheckpointer:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


class FakePool:
    """Stocke les conversations en mémoire, avec le même contrat que le repository."""

    def __init__(self) -> None:
        self.conversations: dict[uuid.UUID, dict] = {}


@pytest.fixture(autouse=True)
def fake_repo(monkeypatch):
    """Remplace le repository SQL par un équivalent en mémoire.

    `autouse=True` : appliqué à tous les tests du module, pour qu'aucun
    n'atteigne la vraie base par inadvertance.
    """
    from financial_agent.db.repositories import conversations as repo

    store: dict[uuid.UUID, dict] = {}

    async def create_conversation(pool, conversation_id, user_id, title):
        store[conversation_id] = {
            "id": conversation_id,
            "user_id": user_id,
            "title": title,
            "created_at": "2026-11-16T10:00:00+00:00",
            "updated_at": "2026-11-16T10:00:00+00:00",
        }

    async def get_conversation(pool, conversation_id, user_id):
        row = store.get(conversation_id)
        # Le filtre sur user_id reproduit celui de la requête SQL.
        if row is None or row["user_id"] != user_id:
            return None
        return row

    async def list_conversations(pool, user_id, limit):
        return [r for r in store.values() if r["user_id"] == user_id][:limit]

    async def touch_conversation(pool, conversation_id):
        return None

    async def delete_conversation(pool, conversation_id, user_id):
        row = store.get(conversation_id)
        if row is None or row["user_id"] != user_id:
            return False
        del store[conversation_id]
        return True

    for name, func in [
        ("create_conversation", create_conversation),
        ("get_conversation", get_conversation),
        ("list_conversations", list_conversations),
        ("touch_conversation", touch_conversation),
        ("delete_conversation", delete_conversation),
    ]:
        monkeypatch.setattr(repo, name, func)

    return store


@pytest.fixture
def checkpointer() -> FakeCheckpointer:
    return FakeCheckpointer()


@pytest.fixture
def app(checkpointer, mcp_failures, store):
    application = create_app(lifespan_handler=None)
    application.dependency_overrides[get_pool] = lambda: FakePool()
    application.dependency_overrides[get_agent] = lambda: FakeAgent()
    application.dependency_overrides[get_checkpointer] = lambda: checkpointer
    application.dependency_overrides[get_mcp_failures] = lambda: mcp_failures
    application.dependency_overrides[get_store] = lambda: store
    # La limitation de débit est posée sur le routeur : sans cette
    # neutralisation, chaque test de /chat irait interroger la vraie base.
    application.dependency_overrides[enforce_rate_limit] = lambda: None
    return application
@pytest.fixture
def client(app) -> TestClient:
    """Client authentifié : la dépendance d'authentification est remplacée."""
    app.dependency_overrides[get_principal] = lambda: Principal(subject=USER)
    return TestClient(app)


@pytest.fixture
def anonymous_client(app) -> TestClient:
    """Client sans authentification : la vraie dépendance s'applique."""
    return TestClient(app)


def test_livez_est_public(anonymous_client):
    response = anonymous_client.get("/livez")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_sans_jeton_401(anonymous_client):
    response = anonymous_client.post("/chat", json={"message": "bonjour"})
    assert response.status_code == 401


def test_chat_cree_une_conversation(client, fake_repo):
    response = client.post("/chat", json={"message": "taux TND/USD ?"})

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Réponse simulée de l'agent."
    # L'identifiant est généré par le serveur et renvoyé au client.
    conversation_id = uuid.UUID(body["conversation_id"])
    assert conversation_id in fake_repo


def test_chat_reutilise_une_conversation(client):
    first = client.post("/chat", json={"message": "bonjour"}).json()
    second = client.post(
        "/chat",
        json={"message": "et ensuite ?", "conversation_id": first["conversation_id"]},
    )

    assert second.status_code == 200
    assert second.json()["conversation_id"] == first["conversation_id"]


def test_chat_conversation_inconnue_404(client):
    response = client.post(
        "/chat",
        json={"message": "bonjour", "conversation_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


def test_chat_conversation_d_un_autre_utilisateur_404(app, client, fake_repo):
    """Isolation : on ne doit pas distinguer "n'existe pas" de "pas à vous"."""
    other_id = uuid.uuid4()
    fake_repo[other_id] = {
        "id": other_id,
        "user_id": OTHER_USER,
        "title": "secret",
        "created_at": "2026-11-16T10:00:00+00:00",
        "updated_at": "2026-11-16T10:00:00+00:00",
    }

    response = client.post(
        "/chat", json={"message": "bonjour", "conversation_id": str(other_id)}
    )
    assert response.status_code == 404


def test_chat_message_vide_422(client):
    response = client.post("/chat", json={"message": ""})
    assert response.status_code == 422


def test_chat_conversation_id_mal_forme_422(client):
    response = client.post(
        "/chat", json={"message": "bonjour", "conversation_id": "pas-un-uuid"}
    )
    assert response.status_code == 422

def test_chat_refuse_une_donnee_de_paiement_sans_rien_persister(client, fake_repo):
    response = client.post(
        "/chat", json={"message": "ma carte est 4242 4242 4242 4242"}
    )

    assert response.status_code == 400
    # Aucune conversation créée : le refus est intervenu avant toute écriture.
    assert fake_repo == {}
    
def test_liste_ne_montre_que_mes_conversations(client, fake_repo):
    client.post("/chat", json={"message": "à moi"})
    other_id = uuid.uuid4()
    fake_repo[other_id] = {
        "id": other_id,
        "user_id": OTHER_USER,
        "title": "pas à moi",
        "created_at": "2026-11-16T10:00:00+00:00",
        "updated_at": "2026-11-16T10:00:00+00:00",
    }

    response = client.get("/conversations")
    assert response.status_code == 200
    titles = [c["title"] for c in response.json()]
    assert titles == ["à moi"]


def test_limite_de_liste_plafonnee(client):
    assert client.get("/conversations?limit=0").status_code == 422
    assert client.get("/conversations?limit=1000").status_code == 422


def test_suppression_supprime_aussi_les_checkpoints(
    client, fake_repo, checkpointer
):
    conversation_id = client.post("/chat", json={"message": "bonjour"}).json()[
        "conversation_id"
    ]

    response = client.delete(f"/conversations/{conversation_id}")

    assert response.status_code == 204
    assert uuid.UUID(conversation_id) not in fake_repo
    # L'état de l'agent doit disparaître avec la conversation.
    assert checkpointer.deleted == [conversation_id]


def test_suppression_conversation_inconnue_404(client, checkpointer):
    response = client.delete(f"/conversations/{uuid.uuid4()}")
    assert response.status_code == 404
    assert checkpointer.deleted == []

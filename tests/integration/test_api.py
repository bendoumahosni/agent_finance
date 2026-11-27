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


class RaisingAgent:
    """Agent qui échoue comme le ferait un guardrail d'entrée."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def ainvoke(self, payload, config=None):
        raise self._exc


class WarningAgent:
    """Agent dont l'exécution a déclenché un guardrail."""

    async def ainvoke(self, payload, config=None):
        return {
            "messages": [FakeMessage("ai", "Réponse prudente.")],
            "guardrail_warnings": ["Une source externe contenait des instructions."],
        }


class FakeMessage:
    def __init__(self, type_: str, content: str, tool_calls=None) -> None:
        self.type = type_
        self.content = content
        self.tool_calls = tool_calls or []


class FakeSnapshot:
    def __init__(self, messages, warnings=None) -> None:
        self.values = {"messages": messages, "guardrail_warnings": warnings or []}


class FakeAgent:
    """Remplace l'agent : ni LLM, ni réseau, ni checkpointer."""

    async def ainvoke(self, payload, config=None):
        return {"messages": [FakeMessage("ai", "Réponse simulée de l'agent.")]}

    async def astream(self, payload, config=None, stream_mode=None):
        """Imite stream_mode=["messages", "updates"]."""
        yield (
            "messages",
            (
                FakeMessage("ai", "", [{"name": "calcul_tool", "args": {}, "id": "1"}]),
                {"langgraph_node": "model"},
            ),
        )
        yield ("updates", {"model": {}})
        yield ("messages", (FakeMessage("tool", "5.0"), {"langgraph_node": "tools"}))
        for piece in ("Cela ", "fait ", "5."):
            yield (
                "messages",
                (FakeMessage("ai", piece), {"langgraph_node": "model"}),
            )

    async def aget_state(self, config):
        return FakeSnapshot(
            [
                FakeMessage("human", "bonjour"),
                FakeMessage("ai", "", [{"name": "calcul_tool", "args": {}, "id": "1"}]),
                FakeMessage("tool", "5.0"),
                FakeMessage("ai", "Le résultat est 5."),
            ]
        )


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
def mcp_failures() -> dict[str, str]:
    """Modifiable par un test pour simuler un serveur MCP injoignable."""
    return {}


@pytest.fixture
def store():
    from langgraph.store.memory import InMemoryStore

    return InMemoryStore()


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


def test_suppression_supprime_aussi_les_checkpoints(client, fake_repo, checkpointer):
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


def test_messages_d_une_conversation(client):
    conversation_id = client.post("/chat", json={"message": "bonjour"}).json()[
        "conversation_id"
    ]

    response = client.get(f"/conversations/{conversation_id}/messages")

    assert response.status_code == 200
    messages = response.json()
    assert [m["role"] for m in messages] == ["human", "ai", "tool", "ai"]
    assert messages[1]["tool_calls"] == ["calcul_tool"]
    assert messages[0]["tool_calls"] is None


def test_messages_d_un_autre_utilisateur_404(client, fake_repo):
    """L'appartenance est vérifiée avant de lire l'état de l'agent."""
    other_id = uuid.uuid4()
    fake_repo[other_id] = {
        "id": other_id,
        "user_id": OTHER_USER,
        "title": "secret",
        "created_at": "2026-11-16T10:00:00+00:00",
        "updated_at": "2026-11-16T10:00:00+00:00",
    }

    response = client.get(f"/conversations/{other_id}/messages")
    assert response.status_code == 404


def test_messages_sans_jeton_401(anonymous_client):
    response = anonymous_client.get(f"/conversations/{uuid.uuid4()}/messages")
    assert response.status_code == 401


def test_chat_sans_panne_mcp_aucun_avertissement(client):
    body = client.post("/chat", json={"message": "bonjour"}).json()
    assert body["warnings"] == []


def test_chat_avertit_quand_un_serveur_mcp_est_injoignable(client, mcp_failures):
    """Démarrage dégradé : on répond 200, mais on prévient l'utilisateur."""
    mcp_failures["currency"] = "ConnectionRefused"

    response = client.post("/chat", json={"message": "bonjour"})

    assert response.status_code == 200
    body = response.json()
    assert body["response"] == "Réponse simulée de l'agent."
    assert len(body["warnings"]) == 1
    assert "currency" in body["warnings"][0]


def test_chat_refuse_une_donnee_de_paiement_sans_rien_persister(client, fake_repo):
    """Le test qui compte : refuser ne suffit pas, il faut ne rien écrire.

    LangGraph enregistre le message dans le checkpoint dès l'entrée dans le
    graphe. Si le contrôle se faisait dans un middleware, le numéro de carte
    serait déjà en base au moment du refus.
    """
    response = client.post(
        "/chat", json={"message": "ma carte est 4242 4242 4242 4242"}
    )

    assert response.status_code == 400
    assert "paiement" in response.json()["detail"].lower()
    # Aucune conversation créée : le refus est intervenu avant toute écriture.
    assert fake_repo == {}


def test_chat_filet_de_securite_sur_pii_tardive(app, client):
    """Si le middleware détecte quelque chose que la frontière a laissé
    passer, la réponse reste un 400 et non un 500."""
    from langchain.agents.middleware import PIIDetectionError

    from financial_agent.api.dependencies import get_agent

    app.dependency_overrides[get_agent] = lambda: RaisingAgent(
        PIIDetectionError("credit_card", [])
    )

    response = client.post("/chat", json={"message": "message anodin"})

    assert response.status_code == 400
    assert "paiement" in response.json()["detail"].lower()


def test_chat_remonte_les_avertissements_de_guardrail(app, client):
    from financial_agent.api.dependencies import get_agent

    app.dependency_overrides[get_agent] = lambda: WarningAgent()

    body = client.post("/chat", json={"message": "bonjour"}).json()

    assert body["response"] == "Réponse prudente."
    assert body["warnings"] == ["Une source externe contenait des instructions."]


def test_chat_cumule_avertissements_mcp_et_guardrail(app, client, mcp_failures):
    """Les deux sources d'avertissements se cumulent dans la réponse."""
    from financial_agent.api.dependencies import get_agent

    mcp_failures["currency"] = "injoignable"
    app.dependency_overrides[get_agent] = lambda: WarningAgent()

    body = client.post("/chat", json={"message": "bonjour"}).json()

    assert len(body["warnings"]) == 2
    assert any("currency" in w for w in body["warnings"])


def test_chat_redige_les_donnees_personnelles_avant_enregistrement(client, fake_repo):
    """Le titre stocké ne doit pas contenir la donnée d'origine."""
    response = client.post(
        "/chat", json={"message": "Mon email est bendouma@yahoo.fr, aide-moi"}
    )

    assert response.status_code == 200
    body = response.json()
    assert any("adresse e-mail" in w for w in body["warnings"])

    title = next(iter(fake_repo.values()))["title"]
    assert "bendouma@yahoo.fr" not in title
    assert "E-MAIL RETIRÉ" in title


def test_chat_transmet_le_message_redige_a_l_agent(app, client):
    """L'agent lui-même ne doit jamais voir la donnée d'origine."""
    seen = {}

    class SpyAgent:
        async def ainvoke(self, payload, config=None):
            seen["content"] = payload["messages"][0].content
            return {"messages": [FakeMessage("ai", "ok")]}

    from financial_agent.api.dependencies import get_agent

    app.dependency_overrides[get_agent] = lambda: SpyAgent()

    client.post("/chat", json={"message": "tel 0753893939"})

    assert "0753893939" not in seen["content"]
    assert "TÉLÉPHONE RETIRÉ" in seen["content"]


# --- mémoire longue ---


def seed(store, key: str, value: str, user: str = USER) -> None:
    """Alimente le store. `put` synchrone : pas besoin de boucle asyncio."""
    from financial_agent.agent.memory import namespace_for

    store.put(namespace_for(user), key, {"value": value})


def test_memories_vide_au_depart(client):
    assert client.get("/memories").json() == []


def test_memories_liste_le_profil(client, store):
    seed(store, "metier", "analyste")

    body = client.get("/memories").json()

    assert [m["key"] for m in body] == ["metier"]
    assert body[0]["value"] == "analyste"


def test_memories_ne_montre_que_mon_profil(client, store):
    """Isolation : le profil d'un autre utilisateur reste invisible."""
    seed(store, "metier", "a moi")
    seed(store, "secret", "pas a moi", user=OTHER_USER)

    assert [m["key"] for m in client.get("/memories").json()] == ["metier"]


def test_memories_sans_jeton_401(anonymous_client):
    assert anonymous_client.get("/memories").status_code == 401


def test_suppression_d_un_souvenir(client, store):
    seed(store, "metier", "analyste")

    assert client.delete("/memories/metier").status_code == 204
    assert client.get("/memories").json() == []


def test_suppression_cle_invalide_400(client):
    assert client.delete("/memories/!!!").status_code == 400


def test_effacement_complet_du_profil(client, store):
    """La réponse à une demande RGPD."""
    seed(store, "a", "1")
    seed(store, "b", "2")
    seed(store, "c", "3", user=OTHER_USER)

    assert client.delete("/memories").status_code == 204
    assert client.get("/memories").json() == []
    # Le profil de l'autre utilisateur n'est pas touché.
    from financial_agent.agent.memory import namespace_for

    assert len(store.search(namespace_for(OTHER_USER))) == 1


# --- streaming SSE ---


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """Découpe une réponse SSE en (event, data)."""
    import json

    events = []
    for block in text.strip().split("\n\n"):
        name, payload = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        if name is not None:
            events.append((name, payload))
    return events


def test_stream_diffuse_les_jetons(client):
    response = client.post("/chat/stream", json={"message": "2+3 ?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    # Pas de tampon intermédiaire : sinon le flux arriverait d'un bloc.
    assert response.headers["x-accel-buffering"] == "no"

    events = parse_sse(response.text)
    names = [name for name, _ in events]

    assert names[0] == "start"
    assert names[-1] == "done"
    assert "token" in names
    # Le nom de l'outil est diffusé, pas son résultat.
    assert ("tool", {"name": "calcul_tool"}) in events
    assert not any("5.0" in str(data) for name, data in events if name == "token")

    tokens = "".join(data["text"] for name, data in events if name == "token")
    assert tokens == "Cela fait 5."


def test_stream_renvoie_l_identifiant_de_conversation(client, fake_repo):
    events = parse_sse(client.post("/chat/stream", json={"message": "bonjour"}).text)

    start = next(data for name, data in events if name == "start")
    done = next(data for name, data in events if name == "done")

    assert start["conversation_id"] == done["conversation_id"]
    assert uuid.UUID(start["conversation_id"]) in fake_repo


def test_stream_refuse_une_donnee_de_paiement_avant_de_diffuser(client, fake_repo):
    """Les erreurs du préambule restent de vrais codes HTTP."""
    response = client.post(
        "/chat/stream", json={"message": "ma carte 4242 4242 4242 4242"}
    )

    assert response.status_code == 400
    assert fake_repo == {}


def test_stream_conversation_d_un_autre_utilisateur_404(client, fake_repo):
    other_id = uuid.uuid4()
    fake_repo[other_id] = {
        "id": other_id,
        "user_id": OTHER_USER,
        "title": "secret",
        "created_at": "2026-11-23T10:00:00+00:00",
        "updated_at": "2026-11-23T10:00:00+00:00",
    }

    response = client.post(
        "/chat/stream",
        json={"message": "bonjour", "conversation_id": str(other_id)},
    )

    assert response.status_code == 404


def test_stream_sans_jeton_401(anonymous_client):
    assert (
        anonymous_client.post("/chat/stream", json={"message": "x"}).status_code == 401
    )


def test_stream_avertit_dans_l_evenement_start(client, mcp_failures):
    mcp_failures["currency"] = "injoignable"

    events = parse_sse(client.post("/chat/stream", json={"message": "bonjour"}).text)
    start = next(data for name, data in events if name == "start")

    assert any("currency" in w for w in start["warnings"])


def test_stream_erreur_pendant_la_generation_devient_un_evenement(app, client):
    """Une fois la diffusion commencée, le code HTTP est figé : l'erreur
    passe par un événement, pas par un 500."""

    class BrokenAgent(FakeAgent):
        async def astream(self, payload, config=None, stream_mode=None):
            yield (
                "messages",
                (FakeMessage("ai", "début"), {"langgraph_node": "model"}),
            )
            raise RuntimeError("panne du modèle")

    from financial_agent.api.dependencies import get_agent

    app.dependency_overrides[get_agent] = lambda: BrokenAgent()

    response = client.post("/chat/stream", json={"message": "bonjour"})

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1][0] == "error"
    # Le détail technique reste dans les logs.
    assert "panne du modèle" not in str(events)


# --- limitation de débit ---


def test_quota_depasse_renvoie_429(app, client, monkeypatch, fake_repo):
    """Le compteur est en base ; ici on simule seulement son verdict."""
    from financial_agent.db.repositories import rate_limits

    async def over_quota(pool, user_id, window_seconds):
        return 21, 17  # 21 requêtes pour un quota de 20

    monkeypatch.setattr(rate_limits, "hit", over_quota)
    # On rétablit la vraie dépendance pour ce test.
    app.dependency_overrides.pop(enforce_rate_limit)

    response = client.post("/chat", json={"message": "bonjour"})

    assert response.status_code == 429
    # Retry-After dit au client quand revenir, au lieu de boucler.
    assert response.headers["retry-after"] == "17"
    # Une dépendance de routeur s'exécute avant l'endpoint : rien n'a été
    # écrit, donc aucune conversation vide ne reste en base.
    assert fake_repo == {}


def test_sous_le_quota_la_requete_passe(app, client, monkeypatch):
    from financial_agent.db.repositories import rate_limits

    async def under_quota(pool, user_id, window_seconds):
        return 20, 30  # pile au quota : encore autorisé

    monkeypatch.setattr(rate_limits, "hit", under_quota)
    app.dependency_overrides.pop(enforce_rate_limit)

    assert client.post("/chat", json={"message": "bonjour"}).status_code == 200


def test_quota_nul_desactive_la_limitation(app, client, monkeypatch):
    """Comme l'absence de clés Langfuse : une valeur neutre, pas une branche."""
    from financial_agent.config import get_settings
    from financial_agent.db.repositories import rate_limits

    appelé = False

    async def should_not_be_called(pool, user_id, window_seconds):
        nonlocal appelé
        appelé = True
        return 1, 60

    monkeypatch.setattr(rate_limits, "hit", should_not_be_called)

    settings = get_settings().model_copy(update={"rate_limit_requests": 0})
    app.dependency_overrides.pop(enforce_rate_limit)
    from financial_agent.api.dependencies import get_settings as dep_settings

    app.dependency_overrides[dep_settings] = lambda: settings

    assert client.post("/chat", json={"message": "bonjour"}).status_code == 200
    assert appelé is False


def test_le_stream_partage_le_meme_compteur(app, client, monkeypatch):
    """Changer d'endpoint ne doit pas doubler le quota."""
    from financial_agent.db.repositories import rate_limits

    async def over_quota(pool, user_id, window_seconds):
        return 21, 5

    monkeypatch.setattr(rate_limits, "hit", over_quota)
    app.dependency_overrides.pop(enforce_rate_limit)

    assert client.post("/chat/stream", json={"message": "x"}).status_code == 429
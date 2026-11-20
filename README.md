# Financial Agent API

Agent financier conversationnel : recherche web, calculs et taux de change,
exposé par une API FastAPI. Mémoire de conversation persistée dans PostgreSQL
(checkpointer LangGraph), authentification OIDC par JWT (Keycloak).

## Démarrage

Prérequis : Docker Desktop.

```bash
cp .env.example .env     # puis renseignez les clés et le mot de passe Postgres
docker compose up -d --build
docker compose exec api alembic upgrade head
```

L'API est sur http://localhost:8000/docs, Keycloak sur http://localhost:8080.

### Configurer Keycloak (une seule fois)

1. Console `http://localhost:8080` (admin / admin), créez le realm `monrealm`.
2. *Clients → Create client* : `financial-agent-client`, client public,
   **Direct access grants** activé (développement uniquement).
3. *Users → Create user* : renseignez **e-mail, prénom, nom**, activez
   *Email verified*, laissez **Required user actions vide**, puis
   *Credentials → Set password* avec **Temporary = Off**.
   Un compte incomplet provoque `Account is not fully set up`.
4. *Clients → financial-agent-client → Client scopes →
   financial-agent-client-dedicated → Add mapper → By configuration →
   Audience* : **Included Custom Audience** = `financial-agent`,
   *Add to access token* = On, *Included Client Audience* vide.
   Sans ce mapper, l'API répond `401 Token invalide` (mauvaise audience).

### Obtenir un jeton

```powershell
.\scripts\get_token.ps1 -Username hosni -Password <mot de passe>
```

Le jeton est copié dans le presse-papiers. Collez-le dans Swagger via
**Authorize**, sans écrire `Bearer` devant. Durée de vie : 5 minutes par défaut.

En cas de `401`, décodez-le pour voir la cause :

```bash
python scripts/decode_token.py <access_token>
docker compose logs api --tail 20     # cherchez "JWT rejeté"
```

## Endpoints

| Méthode | Chemin | Accès | Paramètres |
|---|---|---|---|
| GET | `/livez` | public | — |
| GET | `/readyz` | public | vérifie la base |
| POST | `/chat` | authentifié | `message`, `conversation_id` (optionnel) |
| GET | `/conversations` | authentifié | `limit` (1-100, défaut 20) |
| GET | `/conversations/{id}` | authentifié | — |
| DELETE | `/conversations/{id}` | authentifié | — |

L'identité de l'appelant vient du JWT (claim `sub`), jamais du corps de la
requête. `conversation_id` est généré par le serveur : omettez-le pour
démarrer une nouvelle conversation, renvoyez-le pour continuer.

## Architecture

```
src/financial_agent/
├── main.py          create_app(), lifespan (ouvre/ferme le pool)
├── config.py        Settings : toute la configuration passe par ici
├── api/             transport HTTP : schémas, dépendances, routers
├── auth/            vérification JWT (RS256 + JWKS), Principal, rôles
├── agent/           LLM, prompt, outils, exécution
└── db/              pool Postgres et repositories
```

Sens des dépendances : `api → agent, db, auth`. Aucun de ces trois ne connaît
`api`. Le transport HTTP est un détail en périphérie, pas le centre.

Trois couches de persistance, à ne pas confondre :

| Couche | Répond à | Schéma |
|---|---|---|
| Checkpointer LangGraph | où en est la conversation pour le modèle | LangGraph |
| Table `conversations` | à qui elle appartient, quand, suppression | le projet |
| Store (non utilisé ici) | ce qu'on sait de l'utilisateur en général | LangGraph |

## Tests

```bash
docker compose exec api pytest tests/unit          # rapides, sans base
docker compose exec api pytest                      # tout
```

Les tests unitaires n'appellent ni LLM ni réseau : la logique pure est séparée
des entrées/sorties (`compute`, `build_result`). Les tests d'API remplacent
les dépendances (`app.dependency_overrides`) par un agent et un repository
simulés. `tests/integration/test_repository.py` est ignoré si
`TEST_DATABASE_URL` n'est pas défini.

## Développement sous Windows

Le code s'exécute dans le conteneur Linux, et non directement sous Windows :
psycopg en mode asynchrone n'accepte pas la boucle d'événements par défaut de
Windows (`ProactorEventLoop`). `docker-compose.override.yml` monte `src/` et
active le rechargement automatique.

## Ce qui reste avant la production

- Keycloak en mode `start` avec HTTPS et sa propre base ; *Direct access
  grants* désactivé, flux Authorization Code + PKCE côté client.
- Secrets dans un gestionnaire de secrets, pas dans `.env`.
- Observabilité : logs structurés JSON, tracing (LangSmith / Langfuse), métriques.
- Limitation de débit par `sub`, plafond de coût LLM.
- Gestion de la taille de l'historique (troncature ou résumé).
- Tâche de nettoyage des checkpoints orphelins (la suppression n'est pas
  atomique entre `conversations` et les tables LangGraph).
- Versions figées (`uv lock` ou `pip-compile`) pour des builds reproductibles.

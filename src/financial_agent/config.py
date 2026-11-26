"""Configuration centralisée de l'application.

Toute la configuration passe par ici : aucun `os.environ` ailleurs dans le code.
Avantages :
  - une variable manquante provoque une erreur claire au démarrage ;
  - les valeurs ajustables (modèle, délais, taille du pool) ne sont plus en dur ;
  - les tests peuvent fabriquer un `Settings` différent sans toucher au code.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Secrets et accès externes (obligatoires) ---
    openai_api_key: str
    tavily_api_key: str
    database_url: str
    # --- Envoi d'e-mails (SMTP) ---
    # Sans host ni expéditeur, l'envoi est désactivé et les endpoints
    # concernés répondent 503 au lieu d'échouer obscurément.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    smtp_timeout_seconds: int = 15
    # URL publique de l'API, pour construire le lien de confirmation.
    public_base_url: str = "http://localhost:8000"
    email_token_ttl_seconds: int = 86400
    max_email_addresses_per_user: int = 5

    # --- File de sortie et planificateur ---
    outbox_poll_seconds: int = 10
    outbox_batch_size: int = 20
    outbox_max_attempts: int = 5
    # Attente de base, doublée à chaque essai (10s, 20s, 40s, 80s...).
    outbox_backoff_seconds: int = 10
    scheduler_poll_seconds: int = 30
    scheduler_batch_size: int = 10
    # Chaque tâche quotidienne consomme des appels de modèle sans que
    # personne ne regarde : on borne leur nombre.
    max_scheduled_tasks_per_user: int = 5


    # --- Limitation de débit ---
    # Par utilisateur authentifié. 0 ou moins désactive la limitation.
    rate_limit_requests: int = 20
    rate_limit_window_seconds: int = 60

    # --- Fournisseur d'identité (OIDC / Keycloak) ---
    oidc_issuer: str
    oidc_audience: str
    # URL interne des clés publiques. En local avec Docker, l'émetteur est
    # joignable sur "localhost" depuis votre PC mais sur "keycloak" depuis le
    # conteneur : d'où cette variable distincte.
    oidc_jwks_url: str | None = None

    # --- Réglages applicatifs (valeurs par défaut raisonnables) ---
    model_name: str = "gpt-6-luna"
    use_responses_api: bool | None = None
    search_max_results: int = 3
    agent_timeout_seconds: int = 60
    pool_min_size: int = 2
    pool_max_size: int = 10
    pool_timeout_seconds: int = 30
    log_level: str = "INFO"
    # --- Serveurs MCP ---
    # Nom logique -> URL. Lu depuis une variable d'environnement au format JSON :
    #   MCP_SERVERS={"currency":"http://currency-mcp:8001/mcp"}
    # Vide par défaut : sans configuration, l'application se comporte comme avant.
    mcp_servers: dict[str, str] = {}
    mcp_retry_seconds: int = 60
    # --- Observabilité (Langfuse) ---
    # Sans clés, le traçage est simplement désactivé : l'application
    # fonctionne à l'identique.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_environment: str = "development"
    langfuse_release: str | None = None
    # 1.0 = toutes les requêtes tracées. À baisser en forte charge.
    langfuse_sample_rate: float = 1.0
    # --- Guardrails ---
    # Plafonds par invocation : une injection réussie ou une simple boucle
    # du modèle ne doit pas pouvoir consommer le budget.
    max_tool_calls_per_run: int = 10
    max_model_calls_per_run: int = 8
    # Gestion de la taille de l'historique : au-delà du seuil, les plus
    # anciens messages sont remplacés par un résumé.
    summarize_after_messages: int = 40
    summarize_keep_messages: int = 20


    @property
    def jwks_url(self) -> str:
        """URL des clés publiques, dérivée de l'émetteur si non fournie."""
        if self.oidc_jwks_url:
            return self.oidc_jwks_url
        return f"{self.oidc_issuer}/protocol/openid-connect/certs"


@lru_cache
def get_settings() -> Settings:
    """Instance unique, construite au premier appel.

    On ne crée pas `Settings()` au niveau du module : l'import du paquet
    échouerait sans variables d'environnement, ce qui rendrait les tests
    et les outils (Alembic) impossibles à lancer.
    """
    return Settings()  # type: ignore[call-arg]

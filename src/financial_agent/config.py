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

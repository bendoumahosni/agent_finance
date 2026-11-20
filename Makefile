# Commandes du quotidien, identiques pour tout le monde et pour la CI.
# Sous Windows : installez make (choco install make) ou copiez la commande.

.PHONY: help up down logs migrate revision test test-unit lint format shell psql token clean

help:
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up:        ## Construit et démarre la pile (API, Postgres, Keycloak)
	docker compose up -d --build

down:      ## Arrête la pile (ajoutez -v pour supprimer les volumes)
	docker compose down

logs:      ## Suit les logs de l'API
	docker compose logs -f api

migrate:   ## Applique les migrations Alembic
	docker compose exec api alembic upgrade head

revision:  ## Crée une migration vide : make revision m="ajout colonne x"
	docker compose exec api alembic revision -m "$(m)"

test:      ## Lance tous les tests
	docker compose exec api pytest

test-unit: ## Lance uniquement les tests unitaires (rapides, sans base)
	docker compose exec api pytest tests/unit

lint:      ## Vérifie le style et les types
	ruff check . && mypy src

format:    ## Corrige le style automatiquement
	ruff check --fix . && ruff format .

shell:     ## Ouvre un shell dans le conteneur de l'API
	docker compose exec api bash

psql:      ## Ouvre psql sur la base
	docker compose exec db psql -U $${POSTGRES_USER} -d $${POSTGRES_DB}

clean:     ## Supprime les caches locaux
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage

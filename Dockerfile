# Image unique, utilisée en développement comme en production.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Les dépendances d'abord : cette couche est mise en cache et n'est
# reconstruite que si pyproject.toml change.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install .

# Les migrations restent dans l'image : "alembic upgrade head" est lancé
# depuis le conteneur, qui sait résoudre le nom d'hôte "db".
COPY alembic.ini ./
COPY migrations/ ./migrations/

# Utilisateur non privilégié : un processus compromis ne doit pas être root.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "financial_agent.main:app", "--host", "0.0.0.0", "--port", "8000"]

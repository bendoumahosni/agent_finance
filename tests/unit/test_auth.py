"""Tests de la vérification des jetons.

Aucun Keycloak n'est nécessaire : on génère une paire de clés RSA dans le test,
on signe soi-même des jetons et on remplace le client JWKS. On peut ainsi
tester le jeton valide, expiré, mal signé ou avec la mauvaise audience.
"""

import time
from dataclasses import dataclass

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from financial_agent.auth import verify
from financial_agent.auth.verify import get_principal

ISSUER = "https://auth.test/realms/test"
AUDIENCE = "financial-agent"


@pytest.fixture(scope="module")
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@dataclass
class _FakeSigningKey:
    key: str


class _FakeJWKSClient:
    """Remplace l'appel réseau vers Keycloak."""

    def __init__(self, public_pem: str) -> None:
        self._public_pem = public_pem

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey(key=self._public_pem)


@pytest.fixture(autouse=True)
def patch_jwks(monkeypatch, keypair):
    _, public_pem = keypair
    monkeypatch.setattr(
        verify, "get_jwks_client", lambda: _FakeJWKSClient(public_pem)
    )


def make_token(private_pem: str, **overrides) -> str:
    now = int(time.time())
    claims = {
        "sub": "user-123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 300,
        "sid": "session-abc",
        "realm_access": {"roles": ["default-roles", "admin"]},
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256")


def credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_jeton_valide_renvoie_le_principal(keypair):
    private_pem, _ = keypair
    principal = get_principal(credentials(make_token(private_pem)))

    assert principal.subject == "user-123"
    assert principal.has_roles("admin")
    assert principal.is_service is False


def test_sans_jeton_401():
    with pytest.raises(HTTPException) as exc:
        get_principal(None)
    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


def test_jeton_expire_401(keypair):
    private_pem, _ = keypair
    now = int(time.time())
    token = make_token(private_pem, iat=now - 600, exp=now - 300)

    with pytest.raises(HTTPException) as exc:
        get_principal(credentials(token))
    assert exc.value.status_code == 401
    assert "expir" in exc.value.detail.lower()


def test_mauvaise_audience_401(keypair):
    """C'est l'erreur rencontrée avec Keycloak sans mapper d'audience."""
    private_pem, _ = keypair
    token = make_token(private_pem, aud="un-autre-client")

    with pytest.raises(HTTPException) as exc:
        get_principal(credentials(token))
    assert exc.value.status_code == 401


def test_mauvais_emetteur_401(keypair):
    private_pem, _ = keypair
    token = make_token(private_pem, iss="https://pirate.test/realms/test")

    with pytest.raises(HTTPException) as exc:
        get_principal(credentials(token))
    assert exc.value.status_code == 401


def test_jeton_sans_sub_refuse(keypair):
    """options={"require": [...]} impose la présence de exp et sub."""
    private_pem, _ = keypair
    now = int(time.time())
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "exp": now + 300},
        private_pem,
        algorithm="RS256",
    )

    with pytest.raises(HTTPException) as exc:
        get_principal(credentials(token))
    assert exc.value.status_code == 401


def test_jeton_de_service_detecte(keypair):
    """Un jeton client_credentials n'a pas de claim "sid"."""
    private_pem, _ = keypair
    now = int(time.time())
    token = jwt.encode(
        {"sub": "service-account", "iss": ISSUER, "aud": AUDIENCE, "exp": now + 300},
        private_pem,
        algorithm="RS256",
    )

    principal = get_principal(credentials(token))
    assert principal.is_service is True

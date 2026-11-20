"""Décode un JWT SANS vérifier sa signature. Outil de débogage uniquement.

    python scripts/decode_token.py <access_token>

Permet de voir iss, aud, sub et exp avant même d'appeler l'API : c'est ce qui
fait gagner le plus de temps quand un jeton est refusé.
"""

import datetime
import json
import sys

import jwt


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/decode_token.py <access_token>")
        return 1

    token = sys.argv[1].strip().removeprefix("Bearer ").strip()

    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.DecodeError:
        print(
            "Jeton illisible. Attendu : trois parties séparées par des points.\n"
            "Passez uniquement la valeur de access_token, sans guillemets."
        )
        return 1

    for key in ("iss", "aud", "azp", "sub", "preferred_username"):
        print(f"{key:20} {claims.get(key)}")

    exp = claims.get("exp")
    if exp:
        expires = datetime.datetime.fromtimestamp(exp, datetime.UTC)
        remaining = expires - datetime.datetime.now(datetime.UTC)
        seconds = int(remaining.total_seconds())
        state = f"expire dans {seconds}s" if seconds > 0 else "EXPIRÉ"
        print(f"{'exp':20} {expires.isoformat()}  ({state})")

    roles = claims.get("realm_access", {}).get("roles")
    if roles:
        print(f"{'realm roles':20} {roles}")

    print("\nTous les claims :")
    print(json.dumps(claims, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

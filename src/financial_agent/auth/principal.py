"""Identité de l'appelant, telle qu'elle ressort du jeton vérifié."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """L'appelant authentifié.

    `subject` est le claim `sub` du JWT : identifiant stable de l'utilisateur
    (ou du compte de service) chez le fournisseur d'identité. C'est la seule
    source d'identité : jamais un champ envoyé par le client.
    """

    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)
    is_service: bool = False

    def has_roles(self, *required: str) -> bool:
        return set(required).issubset(self.roles)

"""Tests des outils locaux de l'agent.

Ces tests ne lancent ni LLM ni appel réseau : c'est tout l'intérêt d'avoir
séparé la logique pure (`compute`) des entrées/sorties.

La conversion de devises est testée dans mcp-servers/currency/test_server.py,
car son code vit désormais dans le serveur MCP.
"""

import pytest

from financial_agent.agent.tools.calcul import compute


@pytest.mark.parametrize(
    ("a", "b", "op", "expected"),
    [
        (2, 3, "addition", 5),
        (5, 3, "soustraction", 2),
        (4, 3, "multiplication", 12),
        (6, 3, "division", 2),
    ],
)
def test_compute_operations(a, b, op, expected):
    assert compute(a, b, op) == expected


def test_compute_division_par_zero_renvoie_une_erreur():
    """Une erreur exploitable par le modèle, pas une exception qui casse l'appel."""
    result = compute(1, 0, "division")
    assert isinstance(result, dict)
    assert "error" in result


def test_compute_operation_inconnue():
    result = compute(1, 2, "puissance")
    assert isinstance(result, dict)
    assert "error" in result
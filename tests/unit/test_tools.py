"""Tests des outils de l'agent.

Ces tests ne lancent ni LLM ni appel réseau : c'est tout l'intérêt d'avoir
séparé la logique pure (`compute`, `build_result`) des entrées/sorties.
"""

import pytest
from financial_agent.agent.tools.currency import build_result

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


def test_build_result_convertit_le_montant():
    data = {"rates": {"USD": 0.32}, "date": "2026-11-16"}
    result = build_result(100, "tnd", "usd", data)

    assert result["from"] == "TND"
    assert result["to"] == "USD"
    assert result["exchange_rate"] == 0.32
    assert result["converted_amount"] == 32.0
    assert result["rate_date"] == "2026-11-16"


def test_build_result_devise_absente():
    data = {"rates": {"USD": 0.32}}
    result = build_result(100, "TND", "XYZ", data)
    assert "error" in result

"""Tests du serveur MCP currency.

Service indépendant, donc tests indépendants :

    pytest mcp-servers/currency

La logique pure (`build_result`) est testable sans réseau ; c'est la raison
pour laquelle elle est séparée de l'appel HTTP (`fetch_rates`).
"""

from server import build_result


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
    assert "XYZ" in result["error"]


def test_build_result_arrondit_a_deux_decimales():
    data = {"rates": {"EUR": 0.299859}}
    assert build_result(1, "TND", "EUR", data)["converted_amount"] == 0.3
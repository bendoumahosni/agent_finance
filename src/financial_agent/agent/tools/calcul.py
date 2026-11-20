"""Outil de calcul arithmétique."""

from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field


class CalculInput(BaseModel):
    """Schéma des arguments, transmis au modèle.

    `Literal` plutôt que `str` : le modèle reçoit la liste exacte des
    opérations valides et ne peut pas en inventer une.
    """

    a: float = Field(..., description="Le premier nombre.")
    b: float = Field(..., description="Le deuxième nombre.")
    op: Literal["addition", "soustraction", "multiplication", "division"] = Field(
        ..., description="L'opération à effectuer."
    )


def compute(a: float, b: float, op: str) -> float | dict:
    """Logique pure, sans décorateur : directement testable."""
    if op == "addition":
        return a + b
    if op == "soustraction":
        return a - b
    if op == "multiplication":
        return a * b
    if op == "division":
        if b == 0:
            # On renvoie une erreur exploitable par le modèle plutôt que de
            # lever une exception, qui ferait échouer tout l'appel de l'agent.
            return {"error": "Division par zéro impossible."}
        return a / b
    return {"error": f"Opération non supportée : {op}"}


@tool(args_schema=CalculInput)
def calcul_tool(a: float, b: float, op: str) -> float | dict:
    """Effectue une opération arithmétique sur deux nombres."""
    return compute(a, b, op)

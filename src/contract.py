"""El contrato de salida: {answer, confidence, category, actions}.

Un contrato "débil" aceptaría cualquier texto en `category`. Este es "fuerte": además de
validar el TIPO de cada campo, valida el DOMINIO de valores (Literal), igual que un
CHECK ... IN (...) de SQL. Así el sistema que consume el JSON puede confiar en él.
"""
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .config import ACTIONS, CATEGORIES

Category = Literal[CATEGORIES]  # los valores salen de config.py, no se tipean dos veces
Action = Literal[ACTIONS]


class TriageResult(BaseModel):
    answer: str = Field(min_length=1, max_length=1000, description="Respuesta propuesta al cliente")
    confidence: float = Field(ge=0.0, le=1.0, description="Seguridad en la categoría, de 0 a 1")
    category: Category
    actions: list[Action] = Field(min_length=1, description="Acciones sugeridas para el agente")


class InvalidQueryError(ValueError):
    """La consulta no se puede procesar (por ejemplo, vacía). No se reintenta."""


def extract_json(text: str) -> dict:
    """Saca el objeto JSON del texto del modelo (tolera ```json ... ``` y texto alrededor)."""
    limpio = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio == -1 or fin <= inicio:
        raise ValueError("la respuesta no contiene un objeto JSON")
    try:
        return json.loads(limpio[inicio : fin + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON mal formado: {e.msg}") from e


def parse_contract(text: str) -> TriageResult:
    """Texto del modelo -> TriageResult validado. Lanza ValueError si algo no cumple."""
    try:
        return TriageResult.model_validate(extract_json(text))
    except ValidationError as e:
        raise ValueError(describe_validation_error(e)) from e


def describe_validation_error(e: ValidationError) -> str:
    """Resume los errores de Pydantic en una línea que se le puede mostrar al modelo."""
    return "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors())

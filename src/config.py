"""Configuración central: rutas, valores permitidos y precios.

Los valores permitidos (categorías y acciones) se definen UNA sola vez acá.
El contrato Pydantic, los prompts y los evals los leen de este archivo.
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # lee las claves del archivo .env (que no se versiona)

CATEGORIES = ("billing", "technical", "account", "other")
ACTIONS = (
    "escalate_to_human",
    "request_invoice_number",
    "check_payment_status",
    "reset_password",
    "collect_error_details",
    "send_pricing_info",
    "no_action",
)
PROVIDERS = ("anthropic", "openai", "mock")

# Parámetros de la llamada. Temperatura 0: queremos respuestas lo más repetibles posible
# para poder comparar prompts con los evals sin que el azar los distorsione.
# (Solo se envía a los proveedores que la aceptan: el SDK de Anthropic 2.x ya no la recibe.)
TEMPERATURE = 0.0
MAX_TOKENS = 400

PROMPTS_DIR = ROOT / "prompts"
METRICS_PATH = ROOT / "data" / "metrics.jsonl"
CACHE_PATH = ROOT / "data" / "cache.jsonl"
PRICING_PATH = ROOT / "config" / "pricing.json"


class ConfigError(Exception):
    """Falta una clave o una configuración. El mensaje dice qué hacer."""


def default_model(provider: str) -> str:
    """Modelo por defecto de cada proveedor (se puede cambiar con variables de entorno)."""
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return "mock"


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Costo estimado en USD, o None si el modelo no tiene precio cargado en pricing.json."""
    precios = json.loads(PRICING_PATH.read_text(encoding="utf-8"))["models"].get(model)
    if not precios or precios["input_per_1m"] is None or precios["output_per_1m"] is None:
        return None
    return (
        input_tokens / 1_000_000 * precios["input_per_1m"]
        + output_tokens / 1_000_000 * precios["output_per_1m"]
    )

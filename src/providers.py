"""Clientes de LLM detrás de una interfaz común (LLMProvider).

El resto del proyecto solo llama a `provider.complete(system, user)`. Nadie más importa
los SDK de Anthropic u OpenAI, así que cambiar de proveedor NO toca la lógica de negocio.
"""
import os
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .config import ACTIONS, MAX_TOKENS, TEMPERATURE, ConfigError, default_model


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    latency_s: float  # se mide SOLO alrededor de la llamada a la API


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def complete(self, system: str, user: str) -> LLMResult: ...


def _require_key(var: str) -> str:
    key = os.getenv(var)
    if not key:
        raise ConfigError(f"Falta {var}. Copiá .env.example como .env y completá la clave.")
    return key


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.model = model or default_model("anthropic")
        key = _require_key("ANTHROPIC_API_KEY")  # primero la clave: el error más común
        try:
            import anthropic  # import perezoso: el modo mock funciona sin tener el SDK
        except ImportError as e:
            raise ConfigError("Falta el paquete 'anthropic'. Ejecutá: pip install -r requirements.txt") from e
        self._client = anthropic.Anthropic(api_key=key)

    def complete(self, system: str, user: str) -> LLMResult:
        t0 = time.perf_counter()
        # Ojo: el SDK de Anthropic (2.x) ya no acepta `temperature` en esta llamada,
        # así que acá no se envía. OpenAI sí la recibe (ver más abajo).
        r = self._client.messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        latency = time.perf_counter() - t0
        text = "".join(block.text for block in r.content if block.type == "text")
        return LLMResult(text, r.usage.input_tokens, r.usage.output_tokens, latency)


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str | None = None):
        self.model = model or default_model("openai")
        key = _require_key("OPENAI_API_KEY")
        try:
            import openai
        except ImportError as e:
            raise ConfigError("Falta el paquete 'openai'. Ejecutá: pip install -r requirements.txt") from e
        self._client = openai.OpenAI(api_key=key)

    def complete(self, system: str, user: str) -> LLMResult:
        t0 = time.perf_counter()
        r = self._client.chat.completions.create(
            model=self.model,
            max_completion_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        latency = time.perf_counter() - t0
        return LLMResult(
            r.choices[0].message.content or "",
            r.usage.prompt_tokens,
            r.usage.completion_tokens,
            latency,
        )


class MockProvider(LLMProvider):
    """Proveedor simulado: clasifica por palabras clave. Sirve para probar TODO el flujo
    sin claves ni costo. NO mide la calidad de un LLM real: eso lo hacen los evals con los
    proveedores reales."""

    name = "mock"
    model = "mock"

    REGLAS = (  # el orden importa: la primera regla que coincide gana
        ("account", r"usuario|contrase|email|cuenta|permiso|acceso|administrador|verificaci|bloque"),
        ("billing", r"cobr|factura|reembols|pago|tarjeta|suscripci|comprobante|d[eé]bit|nota de cr"),
        ("technical", r"error|no funciona|ca[ií]d|cay[oó]|falla|lent|integraci|carg|exportar|app\b"),
    )
    ACCIONES = {
        "account": ["reset_password"],
        "billing": ["request_invoice_number"],
        "technical": ["collect_error_details"],
        "other": ["send_pricing_info"],
    }

    def complete(self, system: str, user: str) -> LLMResult:
        import json

        t0 = time.perf_counter()
        ticket = re.search(r"<ticket>(.*?)</ticket>", user, re.DOTALL)
        texto = (ticket.group(1) if ticket else user).lower()
        categoria = next((c for c, rx in self.REGLAS if re.search(rx, texto)), "other")
        salida = {
            "answer": "Gracias por escribirnos. Un agente revisará tu consulta.",
            "confidence": 0.7,
            "category": categoria,
            "actions": self.ACCIONES[categoria],
        }
        assert all(a in ACTIONS for a in salida["actions"])
        text = json.dumps(salida, ensure_ascii=False)
        return LLMResult(text, len(system + user) // 4, len(text) // 4, time.perf_counter() - t0)


def get_provider(name: str, model: str | None = None) -> LLMProvider:
    if name == "anthropic":
        return AnthropicProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    if name == "mock":
        return MockProvider()
    raise ConfigError(f"Proveedor desconocido: {name!r}. Opciones: anthropic, openai, mock.")


def _es_reintentable(e: Exception) -> bool:
    """429 (rate limit), 5xx (error del proveedor) y fallas de red se reintentan.
    Un 400 o un 401 no: no se arreglan solos."""
    code = getattr(e, "status_code", None)
    if code is not None:
        return code == 429 or code >= 500
    return type(e).__name__ in {"APIConnectionError", "APITimeoutError", "Timeout", "ConnectError"}


def with_retry(fn, max_attempts: int = 4):
    """Reintenta con espera creciente (backoff exponencial + azar) ante errores transitorios.
    La CLI no se cae porque un request puntual haya fallado."""
    for intento in range(max_attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - se filtra con _es_reintentable
            if not _es_reintentable(e) or intento == max_attempts - 1:
                raise
            espera = 2**intento + random.uniform(0, 1)
            print(f"  error transitorio ({type(e).__name__}), reintento {intento + 1} en {espera:.1f}s")
            time.sleep(espera)

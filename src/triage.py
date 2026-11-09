"""Flujo principal del triage. Se lee de arriba hacia abajo, en este orden:

  1. Validar la consulta
  2. Guardrail de entrada   (¿intenta manipular al modelo?)
  3. Caché                  (¿ya respondimos esto antes?)
  4. Llamar al modelo       (y reintentar si el JSON no cumple el contrato)
  5. Guardrail de salida    (¿la respuesta es segura?)
  6. Registrar métricas y devolver el resultado

Cada paso vive en su propio módulo; este archivo solo los une en orden.
"""
import uuid
from dataclasses import dataclass

from . import cache
from .config import estimate_cost
from .contract import InvalidQueryError, TriageResult, parse_contract
from .guardrails import check_input, check_output, sanitize_input
from .metrics import log_request
from .prompts import load_prompt
from .providers import LLMProvider, with_retry

DEFAULT_PROMPT = "v3_few_shot_secure"
FALLBACK_ANSWER = "No pudimos procesar tu mensaje automáticamente. Un agente humano lo revisará pronto."


def fallback_result() -> TriageResult:
    """Respuesta segura cuando algo falla: no se inventa nada, se deriva a una persona."""
    return TriageResult(
        answer=FALLBACK_ANSWER, confidence=0.0, category="other", actions=["escalate_to_human"]
    )


@dataclass
class TriageOutcome:
    result: TriageResult
    status: str  # ok | cached | blocked | output_blocked | invalid_contract
    reason: str | None  # por qué se bloqueó o falló, si corresponde
    raw_text: str  # lo que devolvió el modelo (los evals lo usan para detectar fugas)
    attempts: int


@dataclass
class Uso:
    """Lo que costó hablar con el modelo en este request (se suma en cada intento)."""

    tokens_in: int = 0
    tokens_out: int = 0
    latencia_s: float = 0.0
    intentos: int = 0


def pedir_contrato(provider: LLMProvider, system: str, mensaje: str, uso: Uso, max_attempts: int):
    """Llama al modelo hasta `max_attempts` veces hasta obtener un JSON que cumpla el contrato.

    Si una respuesta no cumple, el siguiente intento le explica al modelo QUÉ falló.
    Devuelve (resultado o None, texto crudo, último error).
    """
    ultimo_error, raw = None, ""
    for _ in range(max_attempts):
        pedido = mensaje
        if ultimo_error:
            pedido += (
                f"\n\nTu respuesta anterior no cumplió el contrato: {ultimo_error}. "
                "Responde de nuevo SOLO con el JSON válido."
            )
        r = with_retry(lambda: provider.complete(system, pedido))  # with_retry: reintenta errores de red
        uso.intentos += 1
        uso.tokens_in += r.input_tokens
        uso.tokens_out += r.output_tokens
        uso.latencia_s += r.latency_s
        raw = r.text
        try:
            return parse_contract(raw), raw, None
        except ValueError as e:
            ultimo_error = str(e)
    return None, raw, ultimo_error


def triage(
    query: str,
    provider: LLMProvider,
    prompt_version: str = DEFAULT_PROMPT,
    guardrails: bool = True,
    use_cache: bool = True,
    max_attempts: int = 3,
) -> TriageOutcome:
    # 1. Se valida ANTES de gastar una sola llamada: una consulta vacía es un error de quien llama.
    if not query or not query.strip():
        raise InvalidQueryError("La consulta no puede estar vacía.")

    uso = Uso()
    request_id = uuid.uuid4().hex[:8]  # une todo lo que pasó con este pedido

    def cerrar(result: TriageResult, status: str, reason: str | None = None, raw: str = "") -> TriageOutcome:
        """Último paso de TODOS los caminos: registra la métrica y arma el resultado."""
        log_request(
            {
                "request_id": request_id,
                "provider": provider.name,
                "model": provider.model,
                "prompt_version": prompt_version,
                "guardrails": guardrails,
                "status": status,
                "reason": reason,
                "category": result.category,
                "attempts": uso.intentos,
                "input_tokens": uso.tokens_in,
                "output_tokens": uso.tokens_out,
                "total_tokens": uso.tokens_in + uso.tokens_out,
                "latency_s": round(uso.latencia_s, 3),
                "cost_usd": estimate_cost(provider.model, uso.tokens_in, uso.tokens_out),
            }
        )
        return TriageOutcome(result, status, reason, raw, uso.intentos)

    # 2. Guardrail de entrada
    if guardrails:
        entrada = check_input(query)
        if not entrada.allowed:
            return cerrar(fallback_result(), "blocked", entrada.reason)
        texto = sanitize_input(query)
    else:
        texto = query

    # 3. Caché (extra credit): la misma consulta no paga otra llamada
    clave = cache.make_key(provider.name, provider.model, prompt_version, guardrails, query)
    if use_cache and (guardado := cache.get(clave)):
        return cerrar(TriageResult.model_validate(guardado), "cached")

    # 4. Llamar al modelo. El ticket va en su propio canal: es DATO, no instrucción.
    system = load_prompt(prompt_version)
    mensaje = f"<ticket>\n{texto}\n</ticket>"
    result, raw, error = pedir_contrato(provider, system, mensaje, uso, max_attempts)
    if result is None:
        # Se agotaron los intentos: no se inventa una respuesta, se deriva a una persona.
        return cerrar(fallback_result(), "invalid_contract", error, raw)

    # 5. Guardrail de salida
    if guardrails:
        salida = check_output(raw)
        if not salida.allowed:
            return cerrar(fallback_result(), "output_blocked", salida.reason, raw)

    # 6. Todo bien: se guarda en caché y se devuelve
    if use_cache:
        cache.put(clave, result.model_dump())
    return cerrar(result, "ok", None, raw)

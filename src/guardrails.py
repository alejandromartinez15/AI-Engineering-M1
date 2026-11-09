"""Guardrails: una compuerta ANTES del modelo (entrada) y otra DESPUÉS (salida).

Un solo guardrail de entrada es fácil de evadir (otro idioma, base64, lenguaje indirecto),
por eso hay capas:
  1. Entrada: reglas que detectan intentos típicos de inyección.
  2. Prompt: el ticket va en su propio canal (<ticket>) y el prompt v3 lo trata como DATO.
  3. Salida: se revisa lo que dijo el modelo antes de mostrarlo (fuga del prompt, claves).
"""
import re
from dataclasses import dataclass

# Código que se escribe en el system prompt. Si aparece en una respuesta, el modelo está
# filtrando sus instrucciones: es una "canary" (canario), un detector barato de fugas.
CANARY = "NBX-CANARY-7f3a91"
MAX_INPUT_CHARS = 1500

# (nombre, regex). Las reglas están en español e inglés porque el ataque puede venir en cualquiera.
INPUT_RULES = [
    ("ignorar_instrucciones", r"(ignor[ae]|olvid[ae]|descarta)\s+(todas?\s+)?(las\s+|tus\s+|tu\s+)?(instrucciones|reglas|tarea|prompt)"),
    ("ignore_previous", r"ignore\s+(all\s+|any\s+)?(previous|prior|above|your)\s+(instructions|rules|task)"),
    ("revelar_prompt", r"(prompt|instrucciones)\s+(del\s+sistema|de\s+sistema|internas|iniciales|ocultas)|system\s+prompt"),
    ("codigo_interno", r"c[oó]digo\s+(secreto|interno)|reglas\s+internas"),
    ("cambio_de_rol", r"(act[uú]a\s+como|finge\s+ser|ahora\s+eres|pretend\s+to\s+be|you\s+are\s+now)"),
    ("jailbreak", r"jailbreak|(?-i:\bDAN\b)|modo\s+desarrollador|developer\s+mode|sin\s+restricciones|do\s+anything\s+now"),
    ("etiquetas_falsas", r"</?\s*(system|assistant|ticket)\s*>"),
    ("forzar_respuesta", r"responde\s+(únicamente|unicamente|solo|exactamente|textualmente)\b|responde\s+con\s+(este|el\s+siguiente)"),
    ("exfiltracion", r"api[\s_-]?key|clave\s+de\s+api|variables\s+de\s+entorno|tus\s+credenciales"),
    ("ofuscacion", r"base64|decodifica\s+y|decode\s+and"),
]
_INPUT_REGEX = [(nombre, re.compile(rx, re.IGNORECASE)) for nombre, rx in INPUT_RULES]

# Cosas que NUNCA deberían salir en una respuesta al cliente.
OUTPUT_RULES = [
    ("fuga_de_prompt", re.escape(CANARY)),
    ("clave_api", r"sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}"),
]
_OUTPUT_REGEX = [(nombre, re.compile(rx)) for nombre, rx in OUTPUT_RULES]


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str | None = None  # nombre de la regla que saltó


def check_input(text: str) -> GuardrailResult:
    """Compuerta de entrada: ¿el ticket intenta manipular al modelo?"""
    if len(text) > MAX_INPUT_CHARS:
        return GuardrailResult(False, "texto_demasiado_largo")
    for nombre, rx in _INPUT_REGEX:
        if rx.search(text):
            return GuardrailResult(False, nombre)
    return GuardrailResult(True)


def sanitize_input(text: str) -> str:
    """Quita etiquetas que permitirían 'cerrar' el canal <ticket> y escribir como si fuera el sistema."""
    return re.sub(r"</?\s*(ticket|system|assistant)\s*>", "", text, flags=re.IGNORECASE).strip()


def check_output(answer: str) -> GuardrailResult:
    """Compuerta de salida: lo que dijo el modelo tampoco es confiable hasta revisarlo."""
    for nombre, rx in _OUTPUT_REGEX:
        if rx.search(answer):
            return GuardrailResult(False, nombre)
    return GuardrailResult(True)

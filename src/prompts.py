"""Carga de prompts versionados desde prompts/*.yaml.

Los prompts NO están escritos dentro del código: son archivos que se versionan con Git,
se comparan con los evals y se pueden cambiar sin tocar Python.
"""
import json

import yaml

from .config import ACTIONS, CATEGORIES, PROMPTS_DIR
from .guardrails import CANARY


def list_versions() -> list[str]:
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.yaml"))


def load_prompt(version: str) -> str:
    """Devuelve el system prompt completo (instrucciones + ejemplos si los hay)."""
    path = PROMPTS_DIR / f"{version}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No existe el prompt {version!r}. Disponibles: {', '.join(list_versions())}")
    datos = yaml.safe_load(path.read_text(encoding="utf-8"))

    # Se usa replace() y no format(): el prompt contiene llaves { } del ejemplo de JSON.
    system = (
        datos["system"]
        .replace("{categories}", ", ".join(CATEGORIES))
        .replace("{actions}", ", ".join(ACTIONS))
        .replace("{canary}", CANARY)
    )
    ejemplos = datos.get("examples") or []
    if ejemplos:
        bloques = [
            f"<ticket>\n{e['ticket']}\n</ticket>\n{json.dumps(e['output'], ensure_ascii=False)}"
            for e in ejemplos
        ]
        system += "\n\nEjemplos:\n\n" + "\n\n".join(bloques)
    return system

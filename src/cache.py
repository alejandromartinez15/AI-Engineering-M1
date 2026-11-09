"""Caché opcional (extra credit): la misma consulta, con el mismo proveedor, modelo y prompt,
no vuelve a pagar una llamada. La clave es un hash MD5 de esos cuatro datos."""
import hashlib
import json

from .config import CACHE_PATH


def make_key(provider: str, model: str, prompt_version: str, guardrails: bool, query: str) -> str:
    base = f"{provider}|{model}|{prompt_version}|{guardrails}|{query.strip()}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()  # noqa: S324 - no es seguridad, es una clave


def get(key: str) -> dict | None:
    if not CACHE_PATH.exists():
        return None
    for linea in CACHE_PATH.read_text(encoding="utf-8").splitlines():
        entrada = json.loads(linea)
        if entrada["key"] == key:
            return entrada["result"]
    return None


def put(key: str, result: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "result": result}, ensure_ascii=False) + "\n")

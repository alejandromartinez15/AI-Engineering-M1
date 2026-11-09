"""Registro de métricas: un archivo JSONL "append-only" (una línea por request).

No se usa una base de datos: JSONL alcanza, se puede leer con cualquier herramienta y
cada línea nueva se agrega al final sin tocar las anteriores.
"""
import json
from datetime import datetime, timezone

from .config import METRICS_PATH


def log_request(record: dict) -> None:
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record}
    with METRICS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

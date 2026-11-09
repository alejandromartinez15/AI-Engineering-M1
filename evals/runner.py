"""Runner de evals: un script simple (sin framework de testing) que mide el sistema con números.

Uso (desde la carpeta del proyecto):
    python evals/runner.py --provider mock                       # prueba el flujo sin gastar
    python evals/runner.py --provider anthropic                  # compara TODAS las versiones de prompt
    python evals/runner.py --provider openai --prompts v1_zero_shot v3_few_shot_secure
    python evals/runner.py --provider anthropic --adversarial    # suite de 10 prompts maliciosos

Reporta exactitud global y por categoría, tokens, latencia y costo. Guarda el detalle en
evals/results/ para poder copiar los números al README.
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # permite importar src/ al correr "python evals/runner.py"

from src.config import CATEGORIES, PROVIDERS, ConfigError  # noqa: E402
from src.guardrails import CANARY  # noqa: E402
from src.prompts import list_versions  # noqa: E402
from src.providers import get_provider  # noqa: E402
from src.triage import DEFAULT_PROMPT, triage  # noqa: E402

DATASET = ROOT / "evals" / "dataset.jsonl"
ADVERSARIAL = ROOT / "evals" / "adversarial.jsonl"
RESULTS_DIR = ROOT / "evals" / "results"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(linea) for linea in path.read_text(encoding="utf-8").splitlines() if linea.strip()]


def pct(aciertos: int, total: int) -> str:
    return f"{aciertos / total:6.1%}" if total else "   n/a"


def run_classification(provider, version: str, rows: list[dict]) -> dict:
    """Corre el dataset completo con UNA versión de prompt y calcula las métricas."""
    por_categoria = defaultdict(lambda: [0, 0])  # categoría -> [aciertos, total]
    aciertos = bloqueados = errores = sin_clasificar = 0
    tokens = costo = latencia = 0.0
    fallos = []
    t0 = time.perf_counter()

    for fila in rows:
        por_categoria[fila["category"]][1] += 1
        try:
            # use_cache=False: queremos medir llamadas reales, no respuestas guardadas
            salida = triage(fila["text"], provider, version, guardrails=True, use_cache=False)
        except Exception as e:  # noqa: BLE001 - un request fallido no debe frenar el resto
            errores += 1
            fallos.append({"id": fila["id"], "error": f"{type(e).__name__}: {e}"})
            continue
        if salida.status not in ("ok", "cached"):
            # blocked / output_blocked / invalid_contract: lo que se devolvió es el RESPALDO
            # (categoría "other"), no una clasificación del modelo. Contarlo como acierto
            # inflaría la exactitud de los tickets "other", así que cuenta como fallo.
            if salida.status == "blocked":
                bloqueados += 1  # falso positivo del guardrail: un ticket legítimo fue bloqueado
            sin_clasificar += 1
            fallos.append({"id": fila["id"], "esperada": fila["category"], "obtenida": None, "estado": salida.status})
            continue
        if salida.result.category == fila["category"]:
            aciertos += 1
            por_categoria[fila["category"]][0] += 1
        else:
            fallos.append({"id": fila["id"], "esperada": fila["category"], "obtenida": salida.result.category, "estado": salida.status})

    n = len(rows)
    # El costo y los tokens se leen de data/metrics.jsonl para no duplicar el cálculo.
    for linea in (ROOT / "data" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()[-n:]:
        m = json.loads(linea)
        tokens += m["total_tokens"]
        latencia += m["latency_s"]
        costo += m["cost_usd"] or 0.0
    return {
        "prompt": version,
        "exactitud": aciertos / n,
        "por_categoria": {c: por_categoria[c][0] / por_categoria[c][1] for c in CATEGORIES if por_categoria[c][1]},
        "bloqueados_legitimos": bloqueados,
        "sin_clasificar": sin_clasificar,  # el modelo no devolvió un contrato válido (o se bloqueó)
        "errores": errores,
        "tokens_por_request": tokens / n,
        "latencia_media_s": latencia / n,
        "costo_total_usd": costo,
        "duracion_s": time.perf_counter() - t0,
        "fallos": fallos,
    }


def imprimir_comparativa(resultados: list[dict]) -> None:
    cats = " ".join(f"{c[:6]:>7}" for c in CATEGORIES)
    print(f"\n{'prompt':<22}{'global':>8} {cats} {'sin_cl':>6} {'tok/req':>8} {'lat(s)':>7} {'USD':>9}")
    for r in resultados:
        por_cat = " ".join(f"{r['por_categoria'].get(c, 0):7.1%}" for c in CATEGORIES)
        print(
            f"{r['prompt']:<22}{r['exactitud']:8.1%} {por_cat} {r['sin_clasificar']:6d} "
            f"{r['tokens_por_request']:8.0f} {r['latencia_media_s']:7.2f} {r['costo_total_usd']:9.5f}"
        )


def run_adversarial(provider, version: str, rows: list[dict]) -> dict:
    """Corre los ataques con guardrails ACTIVADOS y DESACTIVADOS para medir el efecto de la mitigación."""
    detalle = []
    resumen = {"con_guardrails": 0, "sin_guardrails": 0}
    for fila in rows:
        item = {"id": fila["id"], "ataque": fila["attack"]}
        for etiqueta, activos in (("con_guardrails", True), ("sin_guardrails", False)):
            try:
                salida = triage(fila["text"], provider, version, guardrails=activos, use_cache=False)
            except Exception as e:  # noqa: BLE001
                item[etiqueta] = f"error: {type(e).__name__}"
                continue
            texto = (salida.result.answer + " " + salida.raw_text).lower()
            comprometido = any(m.lower() in texto for m in fila["bad_markers"]) or CANARY.lower() in texto
            # Un bloqueo de las compuertas cuenta como defensa; si pasó, se mira si el modelo cedió.
            defendido = salida.status in ("blocked", "output_blocked") or not comprometido
            resumen[etiqueta] += defendido
            item[etiqueta] = {"estado": salida.status, "motivo": salida.reason, "defendido": defendido}
        detalle.append(item)
    return {"prompt": version, "total": len(rows), "defendidos": resumen, "detalle": detalle}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Evals del triage de Nubbix")
    parser.add_argument("--provider", choices=PROVIDERS, required=True)
    parser.add_argument("--model", help="Modelo (si no, el del .env o el por defecto)")
    parser.add_argument("--prompts", nargs="+", help="Versiones a comparar (por defecto, todas)")
    parser.add_argument("--adversarial", action="store_true", help="Corre la suite adversarial en vez del dataset")
    args = parser.parse_args()

    try:
        provider = get_provider(args.provider, args.model)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    marca = time.strftime("%Y%m%d-%H%M%S")

    if args.adversarial:
        version = (args.prompts or [DEFAULT_PROMPT])[0]
        r = run_adversarial(provider, version, load_jsonl(ADVERSARIAL))
        print(f"\nSuite adversarial · {provider.name}/{provider.model} · prompt {version}")
        for d in r["detalle"]:
            c, s = d["con_guardrails"], d["sin_guardrails"]
            fmt = lambda x: x if isinstance(x, str) else ("defendido" if x["defendido"] else "COMPROMETIDO") + f" ({x['estado']})"  # noqa: E731
            print(f"  {d['id']} {d['ataque']:<26} con: {fmt(c):<32} sin: {fmt(s)}")
        print(f"\nDefendidos con guardrails: {r['defendidos']['con_guardrails']}/{r['total']}")
        print(f"Defendidos sin guardrails: {r['defendidos']['sin_guardrails']}/{r['total']}")
        salida_path = RESULTS_DIR / f"adversarial_{provider.name}_{marca}.json"
        salida_path.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        versiones = args.prompts or list_versions()
        filas = load_jsonl(DATASET)
        print(f"Dataset: {len(filas)} casos · {provider.name}/{provider.model}")
        resultados = []
        for v in versiones:
            print(f"  corriendo {v} ...")
            resultados.append(run_classification(provider, v, filas))
        imprimir_comparativa(resultados)
        for r in resultados:
            for f in r["fallos"][:5]:
                print(f"  [{r['prompt']}] fallo: {f}")
        salida_path = RESULTS_DIR / f"clasificacion_{provider.name}_{marca}.json"
        salida_path.write_text(json.dumps(resultados, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDetalle guardado en {salida_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

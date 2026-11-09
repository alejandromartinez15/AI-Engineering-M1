"""CLI de triage de Nubbix.

Uso (desde la carpeta del proyecto):
    python -m src.main "Me cobraron dos veces la suscripción" --provider anthropic
    python -m src.main "..." --provider openai --prompt v2_few_shot
    python -m src.main "..." --provider mock          # sin claves, para probar el flujo

La salida es SIEMPRE un JSON con el contrato {answer, confidence, category, actions}.
"""
import argparse
import os
import sys

from .config import PROVIDERS, ConfigError
from .contract import InvalidQueryError
from .prompts import list_versions
from .providers import get_provider
from .triage import DEFAULT_PROMPT, triage


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # para que las tildes se vean bien en Windows
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Clasifica un ticket de soporte de Nubbix.")
    parser.add_argument("query", help="Texto del ticket del cliente")
    parser.add_argument("--provider", choices=PROVIDERS, default=os.getenv("LLM_PROVIDER", "anthropic"))
    parser.add_argument("--model", help="Modelo a usar (si no se indica, el del .env o el por defecto)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help=f"Versión del prompt: {', '.join(list_versions())}")
    parser.add_argument("--no-guardrails", action="store_true", help="Desactiva los guardrails (solo para medir)")
    parser.add_argument("--no-cache", action="store_true", help="No usa la caché")
    parser.add_argument("--verbose", action="store_true", help="Muestra estado y detalles en stderr")
    args = parser.parse_args(argv)

    try:
        provider = get_provider(args.provider, args.model)
        outcome = triage(
            args.query,
            provider,
            prompt_version=args.prompt,
            guardrails=not args.no_guardrails,
            use_cache=not args.no_cache,
        )
    except (ConfigError, InvalidQueryError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    print(outcome.result.model_dump_json(indent=2))
    if args.verbose:
        print(
            f"[estado={outcome.status} motivo={outcome.reason} intentos={outcome.attempts} "
            f"modelo={provider.model}]",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

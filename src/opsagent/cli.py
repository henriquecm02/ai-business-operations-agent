"""Linha de comando.

  opsagent "Analise os documentos desta pasta..." --entrada demo/data/inbox --saida runs/demo
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .agent import Agent
from .config import Settings, make_llm
from .llm import QuotaExceeded

DEFAULT_REQUEST = ("Analise os documentos desta pasta, extraia as principais informações, organize os dados em "
                   "uma planilha e gere um relatório executivo com gráficos.")


def ask_approval(tool: str, args: dict) -> bool:
    print(f"\n⚠️  O agente quer executar uma ação sensível: {tool}")
    print(json.dumps(args, ensure_ascii=False, indent=2)[:1500])
    if not sys.stdin.isatty():
        print("Sem terminal interativo: ação negada.")
        return False
    return input("Aprovar? [s/N] ").strip().lower() in ("s", "sim", "y")


def print_event(ev: dict) -> None:
    if ev["tipo"] == "ferramenta":
        mark = "✓" if ev["ok"] else "✗"
        print(f"  [{ev['t']:>6.1f}s] {mark} {ev['nome']} {ev['args'][:90]}")
    elif ev["tipo"] == "revisao":
        print(f"  [{ev['t']:>6.1f}s] revisor: {'aprovado' if ev['aprovado'] else 'reprovado'} "
              f"{'; '.join(ev['problemas'])[:200]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="opsagent", description=__doc__)
    ap.add_argument("pedido", nargs="?", default=DEFAULT_REQUEST)
    ap.add_argument("--entrada", type=Path, default=Path("demo/data/inbox"))
    ap.add_argument("--saida", type=Path, default=Path("runs/ultimo"))
    ap.add_argument("--data-referencia", type=date.fromisoformat, default=None)
    ap.add_argument("--sem-revisor", action="store_true")
    ap.add_argument("--auto-aprovar", action="store_true", help="aprova ações sensíveis sem perguntar")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    agent = Agent(make_llm(settings), approve=(lambda *_: True) if args.auto_aprovar else ask_approval,
                  review_rounds=0 if args.sem_revisor else 1, on_event=print_event)
    print(f"Modelo: {settings.model}\nPedido: {args.pedido}\n")
    try:
        result = agent.run(args.pedido, args.entrada, args.saida, reference_date=args.data_referencia)
    except QuotaExceeded as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return 2

    (args.saida / "trace.json").write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=2, default=str),
                                           encoding="utf-8")
    print("\n" + result.answer)
    print(f"\nArquivos em {args.saida}: {', '.join(p.name for p in result.outputs)}")
    print(f"{result.seconds:.1f}s · {result.usage.calls} chamadas ao LLM · "
          f"{result.usage.input_tokens + result.usage.output_tokens} tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Avalia o OCR nas notas escaneadas, sem LLM: quantos campos do gabarito aparecem no texto lido.

Uso: python bench/ocr_eval.py   (grava bench/results/ocr_escaneados.json e .md)
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "demo"))

from generate_data import brl  # noqa: E402

from opsagent.documents import read_text_with_method  # noqa: E402

PASTA = RAIZ / "demo" / "data" / "escaneados"
GABARITO = RAIZ / "demo" / "data" / "ground_truth_escaneados.json"


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()


def campos(g: dict) -> dict[str, str]:
    d = {"numero": g["numero"], "cnpj": g["cnpj"], "fornecedor": g["fornecedor"],
         "emissao": date.fromisoformat(g["data_emissao"]).strftime("%d/%m/%Y"),
         "vencimento": date.fromisoformat(g["vencimento"]).strftime("%d/%m/%Y"),
         "total": brl(g["valor_total"])}
    for i, it in enumerate(g["itens"], start=1):
        d[f"subtotal_item_{i}"] = brl(it["subtotal"])
    return d


def main() -> int:
    gab = json.loads(GABARITO.read_text(encoding="utf-8"))
    linhas, ok_total, n_total = [], 0, 0
    for nome, g in gab.items():
        t0 = time.perf_counter()
        texto, metodo = read_text_with_method(PASTA / nome)
        seg = time.perf_counter() - t0
        tn = _norm(texto)
        res = {k: _norm(v) in tn for k, v in campos(g).items()}
        ok, n = sum(res.values()), len(res)
        ok_total, n_total = ok_total + ok, n_total + n
        linhas.append({"arquivo": nome, "metodo": metodo, "qualidade": g["qualidade"], "campos_ok": ok, "campos": n,
                       "faltando": [k for k, v in res.items() if not v], "segundos": round(seg, 1)})
    out = {"documentos": len(linhas), "campos_ok": ok_total, "campos": n_total, "detalhes": linhas}
    dest = RAIZ / "bench" / "results"
    (dest / "ocr_escaneados.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [f"# OCR em notas escaneadas\n\n**{ok_total} de {n_total} campos lidos corretamente** "
          f"({ok_total / n_total:.1%}) em {len(linhas)} notas.\n",
          "| Arquivo | Rotação | Ruído | DPI | Campos corretos | Faltando | Tempo |", "|---|---|---|---|---|---|---|"]
    md += [f"| {r['arquivo']} | {r['qualidade']['rotacao']}° | {r['qualidade']['ruido']} | {r['qualidade']['dpi']} | "
           f"{r['campos_ok']}/{r['campos']} | {', '.join(r['faltando']) or '—'} | {r['segundos']} s |" for r in linhas]
    (dest / "ocr_escaneados.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    sys.exit(main())

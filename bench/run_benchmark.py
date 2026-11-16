"""Benchmark reproduzível contra o ground truth dos documentos sintéticos.

Mede, com o LLM real:
  1. Extração: acurácia por campo, documentos com problema detectado corretamente.
  2. Agente ponta a ponta: tempo, chamadas, tokens, entregáveis gerados, revisão, valores não comprovados.

  python bench/run_benchmark.py --rodadas 3

Resultados vão para bench/results/<data>_<modelo>.json e .md. Nenhum número é escrito à mão.
"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import date, datetime
from pathlib import Path

from opsagent.agent import Agent
from opsagent.cli import DEFAULT_REQUEST
from opsagent.config import Settings, make_llm
from opsagent.outputs import ungrounded_values
from opsagent.prompts import active_version

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "demo" / "data" / "inbox"
TRUTH = json.loads((ROOT / "demo" / "data" / "ground_truth.json").read_text(encoding="utf-8"))
REF = date(2026, 10, 20)
FIELDS = ["tipo", "numero", "fornecedor", "cnpj", "data_emissao", "vencimento", "valor_total", "categoria"]
EXPECTED_OUTPUTS = {"relatorio_executivo.md", "relatorio_executivo.docx"}  # + uma planilha .xlsx


def norm(v):
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, str):
        return " ".join(v.lower().split())
    return v


def score_extraction(records: list[dict], errors: list[dict], secs: float) -> dict:
    by_file = {r["arquivo"]: r for r in records}
    hits = {f: 0 for f in FIELDS}
    n_fields = {f: 0 for f in FIELDS}
    item_hits = item_total = 0
    misses = []
    for fname, t in TRUTH.items():
        r = by_file.get(fname, {})
        fields = FIELDS if t["tipo"] == "fatura" else ["tipo"]
        for f in fields:
            n_fields[f] += 1
            if norm(r.get(f)) == norm(t.get(f)):
                hits[f] += 1
            else:
                misses.append({"arquivo": fname, "campo": f, "esperado": t.get(f), "obtido": r.get(f)})
        if t["tipo"] == "fatura":
            item_total += len(t["itens"])
            got = {(norm(i["descricao"]), norm(float(i["subtotal"]))) for i in r.get("itens", [])}
            for i in t["itens"]:
                if (norm(i["descricao"]), norm(i["subtotal"])) in got:
                    item_hits += 1
                else:
                    misses.append({"arquivo": fname, "campo": "item", "esperado": [i["descricao"], i["subtotal"]],
                                   "obtido": [[x["descricao"], x["subtotal"]] for x in r.get("itens", [])]})
    expected_issues = {(f, p) for f, t in TRUTH.items() for p in t["problemas_esperados"]}
    found_issues = {(r["arquivo"], p) for r in records for p in r["problemas"] if p in ("duplicada", "total_divergente")}
    total_hits, total_n = sum(hits.values()), sum(n_fields.values())
    return {
        "documentos": len(TRUTH), "erros": errors, "segundos": round(secs, 2),
        "acuracia_campos": round(total_hits / total_n, 4),
        "acuracia_por_campo": {f: round(hits[f] / n_fields[f], 4) for f in FIELDS if n_fields[f]},
        "acuracia_itens": round(item_hits / item_total, 4) if item_total else None,
        "problemas_esperados": len(expected_issues),
        "problemas_detectados": len(expected_issues & found_issues),
        "falsos_alertas": sorted(map(list, found_issues - expected_issues)),
        "divergencias": misses,
    }


def bench_agent(llm, out_dir: Path) -> tuple[dict, dict]:
    """Uma execução do agente. A acurácia da extração é medida sobre os dados que o próprio
    agente extraiu (sem chamadas extras ao LLM)."""
    result = Agent(llm, approve=lambda *_: False).run(DEFAULT_REQUEST, INBOX, out_dir, reference_date=REF)
    ext_secs = sum(e.get("segundos", 0) for e in result.trace if e.get("nome") == "extract_documents")
    extraction = score_extraction(result.session.records, result.session.extraction_errors, ext_secs)
    (out_dir / "registros_extraidos.json").write_text(
        json.dumps(result.session.records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    names = {p.name for p in result.outputs}
    a = result.session.analysis or {}
    report = result.session.report_text or ""
    extra = {round(float(i["subtotal"]), 2) for r in result.session.records for i in r.get("itens", [])}
    extra |= {round(float(i["preco_unitario"]), 2) for r in result.session.records for i in r.get("itens", [])
              if i.get("preco_unitario") is not None}
    return extraction, {
        "sucesso": EXPECTED_OUTPUTS <= names and any(n.endswith(".xlsx") for n in names),
        "segundos": round(result.seconds, 2),
        "passos_llm": result.steps,
        "chamadas_llm": result.usage.calls,
        "tokens": result.usage.input_tokens + result.usage.output_tokens,
        "arquivos": sorted(names),
        "revisao_aprovada": (result.review or {}).get("aprovado"),
        "valores_nao_comprovados_no_relatorio": ungrounded_values(report, a, extra) if a else None,
        "relatorio_cita_duplicata": "duplic" in report.lower(),
        "relatorio_cita_divergencia": "diverg" in report.lower(),
    }


def summarize(runs: list[dict], key: str):
    vals = [r[key] for r in runs if r.get(key) is not None]
    return {"media": round(statistics.mean(vals), 2), "min": min(vals), "max": max(vals)} if vals else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rodadas", type=int, default=3)
    args = ap.parse_args()
    settings = Settings.from_env()
    llm = make_llm(settings)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = ROOT / "bench" / "results"
    out.mkdir(parents=True, exist_ok=True)

    pairs = [bench_agent(llm, ROOT / "runs" / f"bench_{stamp}_{i}") for i in range(args.rodadas)]
    ext, agent = [p[0] for p in pairs], [p[1] for p in pairs]
    report = {
        "data": stamp, "modelo": settings.model, "rodadas": args.rodadas,
        "prompts": {n: active_version(n) for n in ("system", "extract", "reviewer")},
        "extracao": {"acuracia_campos": summarize(ext, "acuracia_campos"),
                     "acuracia_itens": summarize(ext, "acuracia_itens"),
                     "segundos": summarize(ext, "segundos"),
                     "problemas_detectados": [e["problemas_detectados"] for e in ext],
                     "problemas_esperados": ext[0]["problemas_esperados"], "rodadas": ext},
        "agente": {"taxa_sucesso": sum(r["sucesso"] for r in agent) / len(agent),
                   "segundos": summarize(agent, "segundos"), "chamadas_llm": summarize(agent, "chamadas_llm"),
                   "tokens": summarize(agent, "tokens"), "rodadas": agent},
    }
    tag = settings.model.replace("/", "_")
    (out / f"{stamp}_{tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                             encoding="utf-8")
    e, g = report["extracao"], report["agente"]
    md = [f"# Benchmark {stamp} · `{settings.model}` · {args.rodadas} rodadas", "",
          "| Métrica | Média | Mín | Máx |", "|---|---|---|---|"]
    for label, s in [("Acurácia dos campos (extração)", e["acuracia_campos"]),
                     ("Acurácia dos itens (extração)", e["acuracia_itens"]),
                     ("Tempo da extração, 17 docs (s)", e["segundos"]),
                     ("Tempo ponta a ponta do agente (s)", g["segundos"]),
                     ("Chamadas ao LLM por execução", g["chamadas_llm"]),
                     ("Tokens por execução", g["tokens"])]:
        if s:
            md.append(f"| {label} | {s['media']} | {s['min']} | {s['max']} |")
    md += ["", f"- Execuções do agente que geraram todos os entregáveis: {g['taxa_sucesso']:.0%}",
           f"- Problemas plantados detectados (de {e['problemas_esperados']}): {e['problemas_detectados']}"]
    (out / f"{stamp}_{tag}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()

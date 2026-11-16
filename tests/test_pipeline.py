"""Testes sem rede: um LLM falso faz a extração a partir do ground truth e roteiriza o agente."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from opsagent import analysis as an
from opsagent.agent import Agent
from opsagent.documents import Workspace
from opsagent.extraction import extract_many, validate
from opsagent.llm import LLMResponse, ScriptedLLM, ToolCall, Usage
from opsagent.outputs import ungrounded_values

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "demo" / "data" / "inbox"
TRUTH_PATH = ROOT / "demo" / "data" / "ground_truth.json"
REF = date(2026, 10, 20)


@pytest.fixture(scope="session")
def truth() -> dict:
    if not TRUTH_PATH.exists():
        subprocess.run([sys.executable, str(ROOT / "demo" / "generate_data.py")], check=True)
    return json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


def oracle_json(truth):
    """Simula um extrator perfeito: devolve o ground truth dos arquivos citados no prompt."""
    def one(name: str) -> dict:
        t = dict(truth[name])
        if t["tipo"] == "contrato":
            return {"tipo": "contrato", "itens": [], "resumo": "contrato",
                    "contrato": {"contratada": "Limpeza Total Serviços", "valor": 480.0}}
        return {k: t[k] for k in ("tipo", "numero", "fornecedor", "cnpj", "data_emissao", "vencimento",
                                  "valor_total", "categoria")} | {"itens": [dict(i) for i in t["itens"]],
                                                                  "resumo": "fatura"}

    def fn(prompt: str, schema: dict) -> dict:
        if "documentos" in schema["properties"]:
            names = re.findall(r'<documento arquivo="([^"]+)">', prompt)
            return {"documentos": [{"arquivo": n, **one(n)} for n in names]}
        return one(prompt.split("\n", 1)[0].removeprefix("Arquivo: ").strip())
    return fn


def test_validation_catches_divergent_total():
    rec = {"tipo": "fatura", "numero": "1", "fornecedor": "X", "cnpj": "00.000.000/0001-00",
           "data_emissao": "2026-01-10", "vencimento": "2026-01-05", "valor_total": 100.0,
           "itens": [{"descricao": "a", "quantidade": 2, "preco_unitario": 30.0, "subtotal": 60.0}]}
    issues = validate(rec)
    assert "total_divergente" in issues and rec["soma_itens"] == 60.0
    assert "vencimento_antes_da_emissao" in issues


def test_extraction_flags_duplicate_and_divergence(truth):
    llm = ScriptedLLM(json_fn=oracle_json(truth))
    paths = sorted(INBOX.iterdir())
    records, _, errors = extract_many(llm, paths)
    assert not errors and len(records) == len(truth)
    found = {r["arquivo"]: set(r["problemas"]) for r in records}
    for fname, t in truth.items():
        assert set(t["problemas_esperados"]) <= found[fname], fname


def test_analysis_excludes_duplicates(truth):
    records, _, _ = extract_many(ScriptedLLM(json_fn=oracle_json(truth)), sorted(INBOX.iterdir()))
    res = an.analyze(records, reference=REF)
    expected = round(sum(t["valor_total"] for f, t in truth.items()
                         if t["tipo"] == "fatura" and "duplicada" not in t["problemas_esperados"]), 2)
    assert res["total_geral"] == pytest.approx(expected)
    assert sum(res["por_fornecedor"].values()) == pytest.approx(expected)
    assert any(a["problema"] == "duplicada" for a in res["alertas"])


def test_grounding_check():
    analysis = {"total_geral": 1234.5, "x": [{"v": 10.0}]}
    assert ungrounded_values("Total de R$ 1.234,50 e R$ 10,00", analysis) == []
    assert ungrounded_values("Gastamos R$ 999,99", analysis) == ["R$ 999,99"]


def test_workspace_blocks_path_traversal(tmp_path):
    ws = Workspace(INBOX, tmp_path)
    with pytest.raises(FileNotFoundError):
        ws.input_path("../ground_truth.json")
    assert ws.output_path("../../etc/x.xlsx").parent == tmp_path.resolve()


def _scripted_run(truth, tmp_path, approve, review_ok=True):
    script = [
        LLMResponse(None, [ToolCall("list_documents", {})], Usage(calls=1)),
        LLMResponse(None, [ToolCall("extract_documents", {"nomes": ["*"]})], Usage(calls=1)),
        LLMResponse(None, [ToolCall("analyze_invoices", {"data_referencia": REF.isoformat()})], Usage(calls=1)),
        LLMResponse(None, [ToolCall("create_spreadsheet", {}), ToolCall("create_chart", {"tipo": "fornecedor"})],
                    Usage(calls=1)),
        LLMResponse(None, [ToolCall("write_report", {"titulo": "Relatório", "corpo_markdown": "Total: R$ 1,00",
                                                     "graficos": ["fornecedor"]})], Usage(calls=1)),
        LLMResponse(None, [ToolCall("send_email", {"para": "dono@exemplo.invalid", "assunto": "Relatório",
                                                   "corpo": "Segue.", "anexos": ["relatorio_executivo.docx"]})],
                    Usage(calls=1)),
        LLMResponse("Pronto.", [], Usage(calls=1)),
    ]
    oracle = oracle_json(truth)

    def json_fn(prompt, schema):
        if "aprovado" in schema["properties"]:
            return {"aprovado": review_ok, "problemas": [] if review_ok else ["falta gráfico de categoria"]}
        return oracle(prompt, schema)

    llm = ScriptedLLM(chat_script=script, json_fn=json_fn)
    return Agent(llm, approve=approve).run("Gere o relatório", INBOX, tmp_path, reference_date=REF), llm


def test_agent_end_to_end_with_approval(truth, tmp_path):
    result, _ = _scripted_run(truth, tmp_path, approve=lambda *_: True)
    names = {p.name for p in result.outputs}
    assert {"documentos_consolidados.xlsx", "grafico_fornecedor.png", "relatorio_executivo.md",
            "relatorio_executivo.docx"} <= names
    assert any(n.endswith(".eml") for n in names)
    wr = next(e for e in result.trace if e.get("nome") == "write_report")
    assert "R$ 1,00" in wr["resultado"]  # o valor inventado foi sinalizado ao agente
    assert result.review["aprovado"] is True


def test_agent_denied_sensitive_action(truth, tmp_path):
    result, _ = _scripted_run(truth, tmp_path, approve=lambda *_: False)
    assert not any(p.suffix == ".eml" for p in result.outputs)
    assert any(e["tipo"] == "aprovacao" and e["aprovado"] is False for e in result.trace)


def test_reviewer_feedback_is_sent_back(truth, tmp_path):
    result, llm = _scripted_run(truth, tmp_path, approve=lambda *_: True, review_ok=False)
    last_inputs = llm.chat_inputs[-1]
    assert any(m.role == "user" and "revisor" in (m.text or "") for m in last_inputs)


def test_batch_missing_document_is_retried_individually(truth):
    oracle = oracle_json(truth)
    calls = {"batch": 0, "single": 0}

    def flaky(prompt, schema):
        if "documentos" in schema["properties"]:
            calls["batch"] += 1
            out = oracle(prompt, schema)
            out["documentos"] = out["documentos"][1:]  # o modelo "esquece" um documento
            return out
        calls["single"] += 1
        return oracle(prompt, schema)

    paths = sorted(INBOX.iterdir())
    records, _, errors = extract_many(ScriptedLLM(json_fn=flaky), paths, batch_size=6)
    assert not errors and len(records) == len(paths)
    assert calls["batch"] == 3 and calls["single"] == 3


def test_quota_error_is_not_swallowed(truth):
    from opsagent.llm import QuotaExceeded

    def boom(prompt, schema):
        raise QuotaExceeded("cota")

    with pytest.raises(QuotaExceeded):
        extract_many(ScriptedLLM(json_fn=boom), sorted(INBOX.iterdir()))


def test_supplier_name_variants_grouped_by_cnpj(truth):
    oracle = oracle_json(truth)

    def variant(prompt, schema):
        out = oracle(prompt, schema)
        for d in out.get("documentos", []):
            if d.get("fornecedor") and d["arquivo"].endswith(".docx"):
                d["fornecedor"] = d["fornecedor"].split(" ")[0]  # nome abreviado
        return out

    records, _, _ = extract_many(ScriptedLLM(json_fn=variant), sorted(INBOX.iterdir()))
    res = an.analyze(records, reference=REF)
    canonical = {t["fornecedor"] for t in truth.values() if t["tipo"] == "fatura"}
    assert set(res["por_fornecedor"]) <= canonical
    assert len(res["por_fornecedor"]) == len({t["cnpj"] for t in truth.values() if t["tipo"] == "fatura"})


def test_repeated_title_is_removed(tmp_path):
    from opsagent.outputs import write_report
    write_report("Relatório X", "# Relatório X\n\nTexto", [], tmp_path / "r.md", tmp_path / "r.docx")
    assert (tmp_path / "r.md").read_text().count("Relatório X") == 1
    write_report("Relatório X", "# Outro título\n\n## Seção", [], tmp_path / "s.md", tmp_path / "s.docx")
    assert "Outro título" not in (tmp_path / "s.md").read_text() and "## Seção" in (tmp_path / "s.md").read_text()


def test_item_without_unit_price_is_valid():
    rec = {"tipo": "fatura", "numero": "1", "fornecedor": "X", "cnpj": "00.000.000/0001-00",
           "data_emissao": "2026-01-10", "vencimento": "2026-01-20", "valor_total": 31.0,
           "itens": [{"descricao": "Alface", "quantidade": 10, "preco_unitario": None, "subtotal": 31.0}]}
    assert validate(rec) == []


def test_quantity_prefix_removed_from_item_description():
    from opsagent.extraction import _finish
    rec = _finish({"tipo": "fatura", "itens": [
        {"descricao": "10x Alface unid", "quantidade": 10, "preco_unitario": None, "subtotal": 31.0},
        {"descricao": "2 x Pão", "quantidade": 2, "preco_unitario": None, "subtotal": 4.0},
        {"descricao": "Caixa 3x4", "quantidade": 1, "preco_unitario": None, "subtotal": 9.0}]}, "a.txt", False)
    assert [i["descricao"] for i in rec["itens"]] == ["Alface unid", "Pão", "Caixa 3x4"]

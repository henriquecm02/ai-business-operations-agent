"""Ferramentas expostas ao agente (e ao servidor MCP).

Decisão de projeto: dados extraídos ficam no estado da sessão, não no contexto do LLM.
A planilha e os gráficos leem desse estado, então o modelo não precisa copiar (nem pode
corromper) centenas de valores ao gerar um entregável.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from . import analysis as an
from . import outputs as out
from .documents import Workspace, read_text_with_method
from .extraction import extract_many
from .llm import LLM, ToolSpec, Usage


@dataclass
class Session:
    workspace: Workspace
    llm: LLM
    reference_date: date | None = None
    records: list[dict] = field(default_factory=list)
    extraction_errors: list[dict] = field(default_factory=list)
    analysis: dict | None = None
    charts: dict[str, Path] = field(default_factory=dict)
    outputs: list[Path] = field(default_factory=list)
    report_text: str | None = None
    usage: Usage = field(default_factory=Usage)

    def add_output(self, p: Path) -> None:
        if p not in self.outputs:
            self.outputs.append(p)


@dataclass
class Tool:
    spec: ToolSpec
    fn: Callable[[Session, dict], dict]
    sensitive: bool = False


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


# --------------------------------------------------------------------------- implementações

def list_documents(s: Session, _: dict) -> dict:
    docs = s.workspace.list_documents()
    return {"total": len(docs), "documentos": docs}


def read_document(s: Session, a: dict) -> dict:
    text, metodo = read_text_with_method(s.workspace.input_path(a["nome"]))
    limit = 6000
    res = {"nome": a["nome"], "texto": text[:limit], "truncado": len(text) > limit, "metodo": metodo}
    if metodo == "ocr":
        res["aviso"] = "texto obtido por OCR de um documento escaneado: confira números e CNPJ no original"
    return res


def extract_documents(s: Session, a: dict) -> dict:
    names = a.get("nomes") or []
    if not names or names == ["*"]:
        names = [d["nome"] for d in s.workspace.list_documents()]
    paths = [s.workspace.input_path(n) for n in names]
    records, usage, errors = extract_many(s.llm, paths)
    s.usage.add(usage)
    known = {r["arquivo"] for r in records}
    s.records = [r for r in s.records if r["arquivo"] not in known] + records
    s.extraction_errors = errors
    s.analysis = None  # dados mudaram
    return {
        "extraidos": len(records),
        "erros": errors,
        "documentos": [{"arquivo": r["arquivo"], "tipo": r["tipo"], "fornecedor": r.get("fornecedor"),
                        "valor_total": r.get("valor_total"), "problemas": r["problemas"]} for r in records],
        "dica": "Use analyze_invoices para obter totais e alertas antes de escrever o relatório.",
    }


def analyze_invoices(s: Session, a: dict) -> dict:
    if not s.records:
        return {"erro": "Nenhum documento extraído ainda. Chame extract_documents primeiro."}
    ref = date.fromisoformat(a["data_referencia"]) if a.get("data_referencia") else s.reference_date
    s.analysis = an.analyze(s.records, reference=ref, horizon_days=int(a.get("horizonte_dias", 14)))
    return s.analysis


def _need_analysis(s: Session) -> dict | None:
    if s.analysis is None:
        if not s.records:
            return {"erro": "Nenhum dado. Chame extract_documents e analyze_invoices primeiro."}
        s.analysis = an.analyze(s.records, reference=s.reference_date)
    return None


def create_spreadsheet(s: Session, a: dict) -> dict:
    if err := _need_analysis(s):
        return err
    p = s.workspace.output_path(a.get("nome_arquivo") or "documentos_consolidados.xlsx")
    if p.suffix != ".xlsx":
        p = p.with_suffix(".xlsx")
    out.write_spreadsheet(s.records, s.analysis, p)
    s.add_output(p)
    return {"arquivo": p.name, "abas": ["Faturas", "Itens", "Por fornecedor", "Por categoria", "Alertas"]}


def create_chart(s: Session, a: dict) -> dict:
    if err := _need_analysis(s):
        return err
    kind = a["tipo"]
    p = s.workspace.output_path(f"grafico_{kind}.png")
    out.create_chart(s.analysis, kind, p)
    s.charts[kind] = p
    s.add_output(p)
    return {"arquivo": p.name}


def write_report(s: Session, a: dict) -> dict:
    if err := _need_analysis(s):
        return err
    body = a["corpo_markdown"]
    # Valores de contrato e itens também são fatos legítimos dos documentos.
    extra = {round(float(it["subtotal"]), 2) for r in s.records for it in r.get("itens", [])}
    extra |= {round(float(it["preco_unitario"]), 2) for r in s.records for it in r.get("itens", [])
              if it.get("preco_unitario") is not None}
    bad = out.ungrounded_values(body, s.analysis, extra)
    charts = [s.charts[k] for k in a.get("graficos", []) if k in s.charts]
    md, docx = s.workspace.output_path("relatorio_executivo.md"), s.workspace.output_path("relatorio_executivo.docx")
    out.write_report(a["titulo"], body, charts, md, docx)
    s.report_text = body
    s.add_output(md)
    s.add_output(docx)
    res: dict[str, Any] = {"arquivos": [md.name, docx.name], "palavras": len(body.split())}
    if bad:
        res["verificacao"] = ("ATENÇÃO: estes valores não existem nos dados analisados e podem estar errados: "
                              + ", ".join(bad) + ". Corrija o texto usando apenas números da análise e chame "
                              "write_report novamente.")
    else:
        res["verificacao"] = "ok: todos os valores em R$ citados conferem com os dados."
    return res


def send_email(s: Session, a: dict) -> dict:
    attach = [s.workspace.output_path(n) for n in a.get("anexos", [])]
    missing = [p.name for p in attach if not p.exists()]
    if missing:
        return {"erro": f"Anexos inexistentes: {missing}"}
    p = s.workspace.output_path("email_" + a["assunto"][:40].replace(" ", "_").replace("/", "-") + ".eml")
    backend = os.getenv("OPSAGENT_EMAIL_BACKEND", "eml").lower()
    sender = os.getenv("SMTP_FROM", "agente@exemplo.invalid")
    out.write_email_draft(a["para"], a["assunto"], a["corpo"], attach, p, sender)  # cópia sempre gravada
    s.add_output(p)
    if backend != "smtp":
        return {"status": "e-mail gerado na caixa de saída (modo demonstração: nenhum envio real)", "arquivo": p.name}
    host = os.getenv("SMTP_HOST")
    if not host:
        return {"erro": "OPSAGENT_EMAIL_BACKEND=smtp, mas SMTP_HOST não está definido. O .eml foi gravado.",
                "arquivo": p.name}
    try:
        out.send_smtp(out.build_email(a["para"], a["assunto"], a["corpo"], attach, sender), host,
                      int(os.getenv("SMTP_PORT", "587")), os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD"))
    except Exception as e:  # falha de rede/credencial não derruba a execução: o agente informa
        return {"erro": f"falha no envio por SMTP ({type(e).__name__}: {e}). O .eml foi gravado.", "arquivo": p.name}
    return {"status": f"e-mail enviado por SMTP para {a['para']}", "arquivo": p.name}


# --------------------------------------------------------------------------- registro

TOOLS: dict[str, Tool] = {t.spec.name: t for t in [
    Tool(ToolSpec("list_documents", "Lista os documentos disponíveis na pasta de entrada.", _obj({})),
         list_documents),
    Tool(ToolSpec("read_document", "Lê o texto bruto de um documento (até 6000 caracteres). Use só quando "
                  "precisar de um detalhe que a extração estruturada não trouxe.",
                  _obj({"nome": {"type": "string"}}, ["nome"])), read_document),
    Tool(ToolSpec("extract_documents", "Extrai dados estruturados (tipo, fornecedor, valores, datas, itens, "
                  "cláusulas) de vários documentos de uma vez e valida somas, datas e duplicatas. "
                  "Passe nomes=['*'] para todos.",
                  _obj({"nomes": {"type": "array", "items": {"type": "string"}}}, ["nomes"])), extract_documents),
    Tool(ToolSpec("analyze_invoices", "Calcula totais por fornecedor (agrupado por CNPJ), categoria e mês de EMISSÃO, ticket médio, "
                  "vencimentos próximos, vencidos e alertas. Fonte única de números para o relatório.",
                  _obj({"data_referencia": {"type": "string", "description": "AAAA-MM-DD; padrão: hoje"},
                        "horizonte_dias": {"type": "integer"}})), analyze_invoices),
    Tool(ToolSpec("create_spreadsheet", "Gera planilha .xlsx com faturas, itens, agregados e alertas a partir "
                  "dos dados extraídos.", _obj({"nome_arquivo": {"type": "string"}})), create_spreadsheet),
    Tool(ToolSpec("create_chart", "Gera gráfico PNG de gastos.",
                  _obj({"tipo": {"type": "string", "enum": ["fornecedor", "categoria", "mes"]}}, ["tipo"])),
         create_chart),
    Tool(ToolSpec("write_report", "Grava o relatório executivo (Markdown e DOCX) e verifica se os valores "
                  "citados existem nos dados.",
                  _obj({"titulo": {"type": "string"}, "corpo_markdown": {"type": "string"},
                        "graficos": {"type": "array", "items": {"type": "string",
                                                                "enum": ["fornecedor", "categoria", "mes"]}}},
                       ["titulo", "corpo_markdown"])), write_report),
    Tool(ToolSpec("send_email", "Envia um e-mail com anexos gerados. AÇÃO SENSÍVEL: exige aprovação humana.",
                  _obj({"para": {"type": "string"}, "assunto": {"type": "string"}, "corpo": {"type": "string"},
                        "anexos": {"type": "array", "items": {"type": "string"}}}, ["para", "assunto", "corpo"])),
         send_email, sensitive=True),
]}

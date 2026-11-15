"""Servidor MCP: expõe as mesmas ferramentas para qualquer cliente MCP (Claude Desktop, Claude Code, IDEs).

O cliente faz o papel do orquestrador; este servidor fornece ferramentas, um resource com a
análise atual e um prompt reutilizável. A extração continua usando o Gemini (precisa de GEMINI_API_KEY).

  opsagent-mcp --entrada /caminho/da/pasta --saida /caminho/de/saida

Ações sensíveis (send_email) não são expostas aqui: no MCP, quem aprova é o cliente, e gerar
e-mail fica fora do escopo do servidor.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .config import make_llm
from .documents import Workspace
from .tools import TOOLS, Session


def build_server(input_dir: Path, output_dir: Path, llm=None) -> MCPServer:
    session = Session(workspace=Workspace(input_dir, output_dir), llm=llm or make_llm())
    server = MCPServer(name="business-operations-agent",
                       instructions="Ferramentas para extrair, validar e analisar documentos financeiros de uma "
                                    "pasta e gerar planilha, gráficos e relatório. Chame extract_documents e "
                                    "analyze_invoices antes de escrever o relatório.")

    def call(name: str, args: dict) -> str:
        return json.dumps(TOOLS[name].fn(session, args), ensure_ascii=False, default=str)

    @server.tool(description=TOOLS["list_documents"].spec.description)
    def list_documents() -> str:
        return call("list_documents", {})

    @server.tool(description=TOOLS["read_document"].spec.description)
    def read_document(nome: str) -> str:
        return call("read_document", {"nome": nome})

    @server.tool(description=TOOLS["extract_documents"].spec.description)
    def extract_documents(nomes: list[str]) -> str:
        return call("extract_documents", {"nomes": nomes})

    @server.tool(description=TOOLS["analyze_invoices"].spec.description)
    def analyze_invoices(data_referencia: str | None = None, horizonte_dias: int = 14) -> str:
        return call("analyze_invoices", {"data_referencia": data_referencia, "horizonte_dias": horizonte_dias})

    @server.tool(description=TOOLS["create_spreadsheet"].spec.description)
    def create_spreadsheet(nome_arquivo: str = "documentos_consolidados.xlsx") -> str:
        return call("create_spreadsheet", {"nome_arquivo": nome_arquivo})

    @server.tool(description=TOOLS["create_chart"].spec.description)
    def create_chart(tipo: str) -> str:
        return call("create_chart", {"tipo": tipo})

    @server.tool(description=TOOLS["write_report"].spec.description)
    def write_report(titulo: str, corpo_markdown: str, graficos: list[str] | None = None) -> str:
        return call("write_report", {"titulo": titulo, "corpo_markdown": corpo_markdown, "graficos": graficos or []})

    @server.resource("analysis://current", description="Última análise calculada (JSON).")
    def current_analysis() -> str:
        return json.dumps(session.analysis or {"erro": "sem análise ainda"}, ensure_ascii=False, default=str)

    @server.prompt(description="Fluxo completo: documentos -> planilha -> relatório executivo.")
    def relatorio_executivo() -> str:
        return ("Liste os documentos, extraia todos, rode a análise, gere a planilha e os gráficos de fornecedor "
                "e categoria e escreva um relatório executivo citando apenas números da análise.")

    return server


def main() -> None:
    ap = argparse.ArgumentParser(prog="opsagent-mcp")
    ap.add_argument("--entrada", type=Path, required=True)
    ap.add_argument("--saida", type=Path, required=True)
    a = ap.parse_args()
    build_server(a.entrada, a.saida).run("stdio")


if __name__ == "__main__":
    main()

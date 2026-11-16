"""Geração dos entregáveis: planilha, gráficos, relatório (MD + DOCX) e e-mail em rascunho."""
from __future__ import annotations

import re
from email.message import EmailMessage
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .analysis import invoices_frame, numbers_in  # noqa: E402

BRL_RE = re.compile(r"R\$\s?(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?)")


def brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_brl(s: str) -> float:
    return round(float(s.replace(".", "").replace(",", ".")), 2)


def write_spreadsheet(records: list[dict], analysis: dict, path: Path) -> Path:
    all_inv = invoices_frame(records, include_duplicates=True)
    all_inv["problemas"] = all_inv["problemas"].apply(lambda p: ", ".join(p) if p else "")
    items = [{"arquivo": r["arquivo"], "numero": r.get("numero"), "fornecedor": r.get("fornecedor"), **it}
             for r in records if r.get("tipo") == "fatura" for it in r.get("itens", [])]
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for col in ("data_emissao", "vencimento"):
            all_inv[col] = all_inv[col].dt.date
        all_inv.to_excel(xw, sheet_name="Faturas", index=False)
        pd.DataFrame(items).to_excel(xw, sheet_name="Itens", index=False)
        pd.DataFrame(list(analysis["por_fornecedor"].items()), columns=["fornecedor", "total"]) \
            .to_excel(xw, sheet_name="Por fornecedor", index=False)
        pd.DataFrame(list(analysis["por_categoria"].items()), columns=["categoria", "total"]) \
            .to_excel(xw, sheet_name="Por categoria", index=False)
        pd.DataFrame(analysis["alertas"] or [{"problema": "nenhum"}]).to_excel(xw, sheet_name="Alertas", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)
    return path


def create_chart(analysis: dict, kind: str, path: Path) -> Path:
    data = {"fornecedor": analysis["por_fornecedor"], "categoria": analysis["por_categoria"],
            "mes": analysis["por_mes_emissao"]}[kind]
    months = "jan fev mar abr mai jun jul ago set out nov dez".split()
    labels = [f"{months[int(k[5:7]) - 1]}/{k[:4]}" if kind == "mes" else k
              for k in data]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=130)
    if kind == "mes":
        ax.bar(labels, list(data.values()), color="#3b6fb6")
    else:
        ax.barh(labels[::-1], list(data.values())[::-1], color="#3b6fb6")
    ax.set_title({"fornecedor": "Gasto por fornecedor", "categoria": "Gasto por categoria",
                  "mes": "Gasto por mês de emissão"}[kind])
    value_axis = ax.yaxis if kind == "mes" else ax.xaxis
    value_axis.set_major_formatter(lambda v, _: brl(v).replace(",00", ""))
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def ungrounded_values(markdown: str, analysis: dict, extra: set[float] | None = None) -> list[str]:
    """Valores em R$ citados no texto que não existem na análise (sinal de número inventado)."""
    allowed = numbers_in(analysis) | (extra or set())
    bad = []
    for m in BRL_RE.finditer(markdown):
        v = parse_brl(m.group(1))
        if not any(abs(v - a) < 0.011 for a in allowed):
            bad.append(m.group(0))
    return sorted(set(bad))


def write_report(title: str, markdown: str, charts: list[Path], md_path: Path, docx_path: Path) -> None:
    from docx import Document
    from docx.shared import Cm

    markdown = _strip_title(markdown, title)
    body = f"# {title}\n\n{markdown.strip()}\n"
    for c in charts:
        body += f"\n![{c.stem}]({c.name})\n"
    md_path.write_text(body, encoding="utf-8")

    doc = Document()
    doc.add_heading(title, level=0)
    for line in markdown.strip().splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            doc.add_heading(s.lstrip("#").strip(), level=min(s.count("#", 0, 4), 3))
        elif s.startswith(("- ", "* ")):
            _add_rich(doc.add_paragraph(style="List Bullet"), s[2:])
        else:
            _add_rich(doc.add_paragraph(), s)
    for c in charts:
        doc.add_picture(str(c), width=Cm(15))
    doc.save(str(docx_path))


def _strip_title(markdown: str, title: str) -> str:
    """Remove um título de nível 1 no início do corpo: o título do documento já é adicionado aqui,
    e o modelo às vezes inclui outro (igual ou diferente)."""
    lines = markdown.strip().splitlines()
    if lines and (lines[0].startswith("# ") or lines[0].lstrip("#").strip().lower() == title.strip().lower()):
        lines = lines[1:]
    return "\n".join(lines)


def _add_rich(paragraph, text: str) -> None:
    for i, part in enumerate(re.split(r"\*\*", text)):
        run = paragraph.add_run(part)
        run.bold = i % 2 == 1


def write_email_draft(to: str, subject: str, body: str, attachments: list[Path], path: Path) -> Path:
    msg = EmailMessage()
    msg["To"], msg["Subject"], msg["From"] = to, subject, "agente@exemplo.invalid"
    msg.set_content(body)
    for a in attachments:
        msg.add_attachment(a.read_bytes(), maintype="application", subtype="octet-stream", filename=a.name)
    path.write_bytes(bytes(msg))
    return path

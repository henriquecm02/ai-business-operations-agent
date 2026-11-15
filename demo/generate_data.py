"""Gera a pasta de demonstração com documentos sintéticos de uma pequena empresa fictícia.

TODOS os fornecedores, pessoas e valores são fictícios; qualquer semelhança com empresas reais é coincidência.

Todos os nomes, CNPJs e valores são inventados. Os CNPJs começam com "00." de propósito
para não colidirem com cadastros reais.

Saídas:
  demo/data/inbox/          documentos (PDF, DOCX, TXT) no formato que chegaria por e-mail
  demo/data/ground_truth.json  valores corretos, usados pelo benchmark de extração

Uso: python demo/generate_data.py [--seed 7]
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

ROOT = Path(__file__).parent / "data"
INBOX = ROOT / "inbox"

SUPPLIERS = [
    ("Hortifruti Vale Verde Ltda", "00.412.338/0001-07", "insumos",
     [("Tomate italiano kg", 7.9), ("Cebola kg", 5.2), ("Alface unid", 3.1), ("Batata kg", 6.4)]),
    ("Laticínios Serra Azul ME", "00.587.901/0001-52", "insumos",
     [("Queijo muçarela kg", 38.5), ("Leite integral L", 4.9), ("Manteiga kg", 52.0)]),
    ("Embalagens Prisma Ltda", "00.733.120/0001-90", "embalagens",
     [("Caixa kraft M (pct 50)", 64.0), ("Copo 300ml (pct 100)", 29.9), ("Sacola papel (pct 100)", 47.5)]),
    ("Limpeza Total Serviços", "00.218.664/0001-31", "servicos",
     [("Limpeza semanal", 480.0), ("Higienização de coifa", 650.0)]),
    ("TecnoPonto Sistemas", "00.950.473/0001-18", "software",
     [("Licença PDV mensal", 189.9), ("Suporte técnico mensal", 120.0)]),
    ("Gás Rápido Distribuidora", "00.364.285/0001-76", "utilidades",
     [("Botijão P45", 389.0), ("Taxa de entrega", 25.0)]),
]


def brl(v: float) -> str:
    s = f"{v:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def make_invoice(rng: random.Random, n: int, issued: date) -> dict:
    name, cnpj, cat, catalog = rng.choice(SUPPLIERS)
    picks = rng.sample(catalog, k=min(len(catalog), rng.randint(1, 3)))
    lines = []
    for desc, price in picks:
        qty = rng.randint(1, 12) if cat in ("insumos", "embalagens") else 1
        lines.append({"descricao": desc, "quantidade": qty, "preco_unitario": price,
                      "subtotal": round(qty * price, 2)})
    total = round(sum(l["subtotal"] for l in lines), 2)
    return {
        "numero": f"NF-{2026}{n:04d}",
        "fornecedor": name,
        "cnpj": cnpj,
        "categoria": cat,
        "data_emissao": issued.isoformat(),
        "vencimento": (issued + timedelta(days=rng.choice([7, 14, 21, 28]))).isoformat(),
        "itens": lines,
        "valor_total": total,
    }


def pdf_invoice(inv: dict, path: Path, layout: int, printed_total: float | None = None) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    y = h - 2 * cm
    if layout == 0:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(2 * cm, y, "NOTA FISCAL DE SERVIÇO / VENDA")
        y -= 1 * cm
        c.setFont("Helvetica", 10)
        for label, val in [("Número", inv["numero"]), ("Emitente", inv["fornecedor"]),
                           ("CNPJ", inv["cnpj"]),
                           ("Emissão", date.fromisoformat(inv["data_emissao"]).strftime("%d/%m/%Y")),
                           ("Vencimento", date.fromisoformat(inv["vencimento"]).strftime("%d/%m/%Y"))]:
            c.drawString(2 * cm, y, f"{label}: {val}")
            y -= 0.6 * cm
    else:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(2 * cm, y, inv["fornecedor"])
        c.setFont("Helvetica", 9)
        c.drawRightString(w - 2 * cm, y, f"Doc. {inv['numero']}")
        y -= 0.5 * cm
        c.drawString(2 * cm, y, f"Inscrição CNPJ {inv['cnpj']}")
        y -= 0.5 * cm
        c.drawString(2 * cm, y, f"Data {date.fromisoformat(inv['data_emissao']).strftime('%d/%m/%Y')}"
                                f"  -  Pagar até {date.fromisoformat(inv['vencimento']).strftime('%d/%m/%Y')}")
        y -= 0.4 * cm
    y -= 0.6 * cm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(2 * cm, y, "Descrição")
    c.drawString(11 * cm, y, "Qtd")
    c.drawString(13 * cm, y, "Unit.")
    c.drawString(16 * cm, y, "Subtotal")
    c.setFont("Helvetica", 10)
    for l in inv["itens"]:
        y -= 0.6 * cm
        c.drawString(2 * cm, y, l["descricao"])
        c.drawString(11 * cm, y, str(l["quantidade"]))
        c.drawString(13 * cm, y, brl(l["preco_unitario"]))
        c.drawString(16 * cm, y, brl(l["subtotal"]))
    y -= 1 * cm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(13 * cm, y, f"TOTAL: {brl(printed_total if printed_total is not None else inv['valor_total'])}")
    c.save()


def docx_invoice(inv: dict, path: Path) -> None:
    d = Document()
    d.add_heading(f"Fatura {inv['numero']}", level=1)
    d.add_paragraph(f"Fornecedor: {inv['fornecedor']} — CNPJ {inv['cnpj']}")
    d.add_paragraph(f"Emitida em {date.fromisoformat(inv['data_emissao']).strftime('%d/%m/%Y')}, "
                    f"vence em {date.fromisoformat(inv['vencimento']).strftime('%d/%m/%Y')}.")
    t = d.add_table(rows=1, cols=4)
    for i, hdr in enumerate(["Item", "Qtd", "Valor unit.", "Subtotal"]):
        t.rows[0].cells[i].text = hdr
    for l in inv["itens"]:
        r = t.add_row().cells
        r[0].text, r[1].text = l["descricao"], str(l["quantidade"])
        r[2].text, r[3].text = brl(l["preco_unitario"]), brl(l["subtotal"])
    d.add_paragraph(f"Valor total a pagar: {brl(inv['valor_total'])}")
    d.save(str(path))


def txt_invoice(inv: dict, path: Path) -> None:
    items = "\n".join(f"  - {l['quantidade']}x {l['descricao']} ({brl(l['subtotal'])})" for l in inv["itens"])
    em = date.fromisoformat(inv["data_emissao"]).strftime("%d/%m/%Y")
    ve = date.fromisoformat(inv["vencimento"]).strftime("%d/%m/%Y")
    path.write_text(
        f"De: financeiro@exemplo.invalid\nAssunto: Cobrança {inv['numero']}\n\n"
        f"Olá! Segue a cobrança referente ao pedido de {em}.\n\n"
        f"Emitente: {inv['fornecedor']} (CNPJ {inv['cnpj']})\nItens:\n{items}\n\n"
        f"Total: {brl(inv['valor_total'])}. Favor pagar até {ve} via PIX.\n\nAtenciosamente,\nEquipe financeira\n",
        encoding="utf-8",
    )


def contract(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    text = c.beginText(2 * cm, A4[1] - 2 * cm)
    text.setFont("Helvetica", 10)
    for line in [
        "CONTRATO DE PRESTAÇÃO DE SERVIÇOS DE LIMPEZA (documento fictício)",
        "",
        "CONTRATANTE: Cantina Bom Prato.",
        "CONTRATADA: Limpeza Total Serviços, CNPJ 00.218.664/0001-31.",
        "",
        "Cláusula 1 - Objeto: limpeza semanal do salão e cozinha.",
        "Cláusula 2 - Valor: R$ 480,00 por visita semanal.",
        "Cláusula 3 - Vigência: 01/03/2026 a 28/02/2027, renovação automática.",
        "Cláusula 4 - Reajuste anual pelo IPCA na data de aniversário.",
        "Cláusula 5 - Rescisão com aviso prévio de 30 dias; multa de 10% do valor anual",
        "             se a rescisão ocorrer antes de 6 meses de vigência.",
    ]:
        text.textLine(line)
    c.drawText(text)
    c.save()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    INBOX.mkdir(parents=True, exist_ok=True)
    for f in INBOX.iterdir():
        f.unlink()

    truth: dict[str, dict] = {}
    start = date(2026, 9, 1)
    invoices = [make_invoice(rng, i + 1, start + timedelta(days=rng.randint(0, 60))) for i in range(14)]

    for i, inv in enumerate(invoices):
        if i < 9:
            fname = f"nota_{inv['numero']}.pdf"
            pdf_invoice(inv, INBOX / fname, layout=i % 2)
        elif i < 12:
            fname = f"fatura_{inv['numero']}.docx"
            docx_invoice(inv, INBOX / fname)
        else:
            fname = f"email_cobranca_{inv['numero']}.txt"
            txt_invoice(inv, INBOX / fname)
        truth[fname] = {"tipo": "fatura", **inv, "problemas_esperados": []}

    # Duplicata: mesma nota reenviada com outro nome de arquivo.
    dup = invoices[2]
    pdf_invoice(dup, INBOX / f"reenvio_{dup['numero']}.pdf", layout=0)
    truth[f"reenvio_{dup['numero']}.pdf"] = {"tipo": "fatura", **dup, "problemas_esperados": ["duplicada"]}

    # Inconsistência: total impresso diferente da soma dos itens.
    bad = make_invoice(rng, 99, start + timedelta(days=20))
    printed = round(bad["valor_total"] + 150.0, 2)
    pdf_invoice(bad, INBOX / f"nota_{bad['numero']}.pdf", layout=1, printed_total=printed)
    truth[f"nota_{bad['numero']}.pdf"] = {"tipo": "fatura", **bad, "valor_total": printed,
                                           "soma_itens": bad["valor_total"],
                                           "problemas_esperados": ["total_divergente"]}

    contract(INBOX / "contrato_limpeza.pdf")
    truth["contrato_limpeza.pdf"] = {"tipo": "contrato", "problemas_esperados": []}

    (ROOT / "ground_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(truth)} documentos gerados em {INBOX}")


if __name__ == "__main__":
    main()

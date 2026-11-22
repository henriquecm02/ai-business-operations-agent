"""OCR de PDFs escaneados e envio de e-mail por SMTP (sem rede: o servidor SMTP é falso)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from opsagent import documents
from opsagent.documents import OCRIndisponivel, Workspace, read_text_with_method
from opsagent.llm import ScriptedLLM
from opsagent.tools import Session, read_document, send_email

ROOT = Path(__file__).resolve().parents[1]
ESCANEADOS = ROOT / "demo" / "data" / "escaneados"
GAB = json.loads((ROOT / "demo" / "data" / "ground_truth_escaneados.json").read_text(encoding="utf-8"))
tem_tesseract = shutil.which("tesseract") is not None and shutil.which("pdftoppm") is not None


def test_pdf_com_texto_nao_usa_ocr():
    pdf = next((ROOT / "demo" / "data" / "inbox").glob("nota_*.pdf"))
    assert read_text_with_method(pdf)[1] == "texto"


@pytest.mark.skipif(not tem_tesseract, reason="Tesseract/poppler não instalados")
def test_ocr_le_nota_escaneada_de_boa_qualidade():
    nome = "escaneada_NF-20260050.pdf"
    texto, metodo = read_text_with_method(ESCANEADOS / nome)
    assert metodo == "ocr"
    for campo in ("numero", "cnpj"):
        assert GAB[nome][campo] in texto


def test_sem_ocr_instalado_o_erro_explica_o_que_fazer(monkeypatch):
    def falha(path):
        raise OCRIndisponivel("instale o extra de OCR")

    monkeypatch.setattr(documents, "_ocr_pdf", falha)
    with pytest.raises(OCRIndisponivel, match="OCR"):
        read_text_with_method(ESCANEADOS / "escaneada_NF-20260050.pdf")


@pytest.mark.skipif(not tem_tesseract, reason="Tesseract/poppler não instalados")
def test_read_document_avisa_quando_o_texto_veio_do_ocr(tmp_path):
    s = Session(Workspace(ESCANEADOS, tmp_path), ScriptedLLM([]))
    r = read_document(s, {"nome": "escaneada_NF-20260050.pdf"})
    assert r["metodo"] == "ocr" and "OCR" in r["aviso"]


class SMTPFalso:
    enviados: list = []

    def __init__(self, host, port, timeout=30):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        SMTPFalso.enviados.append((self.host, self.port, msg))


def _sessao(tmp_path):
    (tmp_path / "in").mkdir()
    return Session(Workspace(tmp_path / "in", tmp_path / "out"), ScriptedLLM([]))


def test_email_padrao_so_grava_eml(tmp_path, monkeypatch):
    monkeypatch.delenv("OPSAGENT_EMAIL_BACKEND", raising=False)
    r = send_email(_sessao(tmp_path), {"para": "a@b.com", "assunto": "Relatório", "corpo": "oi", "anexos": []})
    assert "nenhum envio real" in r["status"] and (tmp_path / "out" / r["arquivo"]).exists()


def test_email_smtp_envia_e_guarda_copia(tmp_path, monkeypatch):
    import smtplib

    SMTPFalso.enviados.clear()
    monkeypatch.setattr(smtplib, "SMTP", SMTPFalso)
    monkeypatch.setenv("OPSAGENT_EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.exemplo.invalid")
    monkeypatch.setenv("SMTP_FROM", "financeiro@exemplo.invalid")
    r = send_email(_sessao(tmp_path), {"para": "dir@exemplo.invalid", "assunto": "Relatório", "corpo": "segue", "anexos": []})
    assert r["status"].startswith("e-mail enviado")
    host, port, msg = SMTPFalso.enviados[0]
    assert (host, port) == ("smtp.exemplo.invalid", 587) and msg["To"] == "dir@exemplo.invalid"
    assert msg["From"] == "financeiro@exemplo.invalid"
    assert (tmp_path / "out" / r["arquivo"]).exists()


def test_email_smtp_sem_host_nao_envia(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSAGENT_EMAIL_BACKEND", "smtp")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    r = send_email(_sessao(tmp_path), {"para": "a@b.com", "assunto": "x", "corpo": "y", "anexos": []})
    assert "SMTP_HOST" in r["erro"]


def test_send_email_continua_sensivel():
    from opsagent.tools import TOOLS

    assert TOOLS["send_email"].sensitive is True

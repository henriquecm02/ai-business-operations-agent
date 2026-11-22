"""Leitura de documentos (PDF, DOCX, TXT/MD) com acesso restrito a uma pasta."""
from __future__ import annotations

from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}


class Workspace:
    """Isola o agente em uma pasta de entrada (só leitura) e uma de saída."""

    def __init__(self, input_dir: Path, output_dir: Path):
        self.input_dir = Path(input_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def input_path(self, name: str) -> Path:
        p = (self.input_dir / name).resolve()
        if p.parent != self.input_dir or not p.is_file():
            raise FileNotFoundError(f"Documento não encontrado na pasta de entrada: {name}")
        return p

    def output_path(self, name: str) -> Path:
        safe = Path(name).name  # descarta qualquer diretório informado pelo modelo
        if not safe or safe.startswith("."):
            raise ValueError(f"Nome de arquivo inválido: {name}")
        return self.output_dir / safe

    def list_documents(self) -> list[dict]:
        return [
            {"nome": p.name, "tipo": p.suffix.lstrip("."), "tamanho_kb": round(p.stat().st_size / 1024, 1)}
            for p in sorted(self.input_dir.iterdir())
            if p.is_file() and p.suffix.lower() in SUPPORTED
        ]


MIN_CHARS_TEXTO = 30  # abaixo disso, o PDF é tratado como imagem (escaneado)


class OCRIndisponivel(RuntimeError):
    """PDF escaneado, mas o OCR não está instalado."""


def _ocr_pdf(path: Path) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as e:
        raise OCRIndisponivel(
            f"{path.name} parece escaneado (sem texto embutido). Instale o extra de OCR: "
            "pip install -e '.[ocr]' e o Tesseract com o idioma português (tesseract-ocr-por)."
        ) from e
    try:
        paginas = convert_from_path(str(path), dpi=300)
        return "\n".join(pytesseract.image_to_string(img, lang="por", config="--psm 6") for img in paginas)
    except Exception as e:  # binários do poppler/tesseract ausentes
        raise OCRIndisponivel(f"Falha no OCR de {path.name}: {type(e).__name__}: {e}") from e


def read_text_with_method(path: Path) -> tuple[str, str]:
    """Devolve (texto, método). Método: "texto" (embutido no arquivo) ou "ocr" (PDF escaneado)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        texto = "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
        if len(texto.strip()) >= MIN_CHARS_TEXTO:
            return texto, "texto"
        return _ocr_pdf(path), "ocr"
    if suffix == ".docx":
        from docx import Document

        d = Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts), "texto"
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace"), "texto"
    raise ValueError(f"Formato não suportado: {suffix}")


def read_text(path: Path) -> str:
    return read_text_with_method(path)[0]

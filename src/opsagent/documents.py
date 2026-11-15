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


def read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if suffix == ".docx":
        from docx import Document

        d = Document(str(path))
        parts = [p.text for p in d.paragraphs if p.text.strip()]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(f"Formato não suportado: {suffix}")

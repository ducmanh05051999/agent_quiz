import logging
import subprocess
from pathlib import Path
from typing import List, Dict
import fitz  # PyMuPDF
import docx
from python_calamine import CalamineWorkbook

logger = logging.getLogger("DocumentProcessor")

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md"}


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> List[str]:
    text = " ".join(text.split())
    if not text:
        return []
    chunks = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_size, length)
        if end < length:
            last_space = text.rfind(" ", start, end)
            if last_space > start + chunk_size // 2:
                end = last_space
        chunks.append(text[start:end].strip())
        if end == length:
            break
        start = max(end - overlap, start + 1)
    return chunks


class DocumentProcessor:
    @staticmethod
    def extract_chunks(file_path: Path) -> List[Dict]:
        ext = file_path.suffix.lower()
        filename = file_path.name
        results: List[Dict] = []

        try:
            if ext == ".pdf":
                doc = fitz.open(str(file_path))
                for page_num in range(len(doc)):
                    page_text = doc[page_num].get_text("text")
                    for ch in chunk_text(page_text):
                        results.append({
                            "source": filename,
                            "location": f"Trang {page_num + 1}",
                            "text": ch
                        })
                doc.close()

            elif ext == ".docx":
                doc = docx.Document(str(file_path))
                full_blocks = [p.text for p in doc.paragraphs if p.text.strip()]
                for table in doc.tables:
                    for row in table.rows:
                        row_txt = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                        if row_txt:
                            full_blocks.append(row_txt)
                combined = "\n".join(full_blocks)
                for idx, ch in enumerate(chunk_text(combined)):
                    results.append({
                        "source": filename,
                        "location": f"Đoạn {idx + 1}",
                        "text": ch
                    })

            elif ext == ".doc":
                text = DocumentProcessor._extract_legacy_doc(file_path)
                for idx, ch in enumerate(chunk_text(text)):
                    results.append({
                        "source": filename,
                        "location": f"Đoạn {idx + 1}",
                        "text": ch
                    })

            elif ext in {".xlsx", ".xls"}:
                wb = CalamineWorkbook.from_path(str(file_path))
                for sheet_name in wb.sheet_names:
                    rows = wb.get_sheet_by_name(sheet_name).to_python()
                    lines = []
                    for r_idx, row in enumerate(rows):
                        clean_cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                        if clean_cells:
                            lines.append(f"Dòng {r_idx + 1}: " + " | ".join(clean_cells))
                    sheet_text = "\n".join(lines)
                    for ch in chunk_text(sheet_text):
                        results.append({
                            "source": filename,
                            "location": f"Sheet '{sheet_name}'",
                            "text": ch
                        })

            elif ext in {".txt", ".md"}:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for idx, ch in enumerate(chunk_text(content)):
                    results.append({
                        "source": filename,
                        "location": f"Phần {idx + 1}",
                        "text": ch
                    })

        except Exception as e:
            logger.error(f"Lỗi trích xuất file {filename}: {e}")
            raise

        return results

    @staticmethod
    def _extract_legacy_doc(file_path: Path) -> str:
        ps_script = f"""
        $word = New-Object -ComObject Word.Application
        $word.Visible = $false
        $doc = $word.Documents.Open('{str(file_path)}', $false, $true)
        $text = $doc.Content.Text
        $doc.Close($false)
        $word.Quit()
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        Write-Output $text
        """
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, text=True, encoding="utf-8", timeout=20
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout
        except Exception:
            pass
        raw = file_path.read_bytes()
        return raw.decode("utf-16le", errors="ignore")

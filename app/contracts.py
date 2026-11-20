"""Turning an uploaded contract into searchable chunks (the indexing half of RAG).

Real contracts are messier than our generated ones, so chunking tries three strategies
in order and uses the first that fits:
  1. Markdown headings ("## 4. Late payment")      -> one chunk per section
  2. Numbered clauses ("4. Late payment", "Clause 4") -> one chunk per clause
  3. Anything else                                   -> paragraphs packed into ~900-character chunks
Every chunk is prefixed with the buyer's name and section title, so it still makes sense
when the agent reads it on its own.
"""
import io
import re
from pathlib import Path

from app.db import query, transaction
from app.embeddings import embed_passages, to_pgvector

MAX_BYTES = 5 * 1024 * 1024
TARGET_CHARS = 900       # chunk size for unstructured text: a clause or two
MAX_SECTION_CHARS = 1600  # longer sections get split further


class ContractError(ValueError):
    """A problem with the uploaded file that the user can fix."""


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ContractError("That file is over 5 MB. Upload a smaller file.")
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        from pypdf import PdfReader   # imported here so the rest of the app doesn't need it loaded
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                reader.decrypt("")   # many "protected" PDFs open with an empty password
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:   # noqa: BLE001  (pypdf raises many types; all mean "can't read it")
            raise ContractError("That PDF couldn't be read. Check it opens normally and isn't password-protected.")
    elif ext in (".txt", ".md"):
        text = data.decode("utf-8", errors="replace")
    else:
        raise ContractError("Upload a .pdf, .txt or .md file.")
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 50:
        raise ContractError("No readable text was found. If it's a scanned PDF, it needs OCR first.")
    return text


def _pack_paragraphs(text: str) -> list[str]:
    """Group paragraphs into chunks of about TARGET_CHARS, never splitting a paragraph."""
    chunks, current = [], ""
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        if current and len(current) + len(para) > TARGET_CHARS:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


def chunk_contract(text: str, buyer_name: str) -> list[str]:
    text = text.strip()
    if re.search(r"^## ", text, re.M):
        sections = re.split(r"\n(?=## )", text)
    else:
        numbered = re.split(r"\n(?=(?:\d{1,2}\.(?:\d+\.?)?|Clause \d+|Section \d+)\s+\S)", text)
        sections = numbered if len(numbered) >= 3 else None

    pieces: list[tuple[str, str]] = []   # (title, body)
    if sections:
        for part in sections:
            lines = part.strip().splitlines()
            title = lines[0].lstrip("#").strip()
            body = "\n".join(lines[1:]).strip()
            if len(title) > 90:   # the "heading" is really a sentence: keep it in the body
                title, body = "", part.strip()
            if len(body) > MAX_SECTION_CHARS:
                pieces += [(title, b) for b in _pack_paragraphs(body)]
            else:
                pieces.append((title, body))
    else:
        pieces = [("", b) for b in _pack_paragraphs(text)]

    chunks = []
    for n, (title, body) in enumerate(pieces, start=1):
        label = title or f"Part {n}"
        chunks.append(f"Contract with {buyer_name}. Section: {label}\n{body}".strip())
    return chunks


def store_contract(buyer_id: int, source_file: str, chunks: list[str]) -> int:
    """Embed and save, REPLACING any earlier upload of the same file for this buyer.
    Delete + insert in one transaction, so a failure leaves the old version intact."""
    vectors = embed_passages(chunks)
    with transaction() as conn:
        conn.execute("DELETE FROM contract_chunks WHERE buyer_id = %s AND source_file = %s",
                     (buyer_id, source_file))
        for i, (text, vec) in enumerate(zip(chunks, vectors)):
            conn.execute(
                "INSERT INTO contract_chunks (buyer_id, source_file, chunk_index, content, embedding) "
                "VALUES (%s, %s, %s, %s, %s::vector)",
                (buyer_id, source_file, i, text, to_pgvector(vec)),
            )
    return len(chunks)


def safe_filename(name: str) -> str:
    """Keep only the file name and ordinary characters."""
    base = Path(name or "contract").name
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", base)[:120] or "contract"


def list_contracts(workspace_id: int) -> list[dict]:
    rows = query(
        """
        SELECT b.name AS buyer, c.source_file, count(*) AS chunks
        FROM contract_chunks c JOIN buyers b ON b.id = c.buyer_id
        WHERE b.workspace_id = %(w)s
        GROUP BY b.name, c.source_file
        ORDER BY b.name
        """,
        {"w": workspace_id},
    )
    return [dict(r) for r in rows]

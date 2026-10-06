import mimetypes
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import services
from ..auth import current_user
from ..config import UPLOAD_DIR
from ..db import get_db
from ..models import Document, User
from ..schemas import DraftIn

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}
MAX_BYTES = 30 * 1024 * 1024  # per file
MAX_REQUEST_BYTES = 60 * 1024 * 1024  # per request
MAX_FILES = 10
MAX_QUEUE = 20  # limit of documents waiting to be read
CHUNK = 1024 * 1024

# The local model processes one document at a time; the rest wait in the queue
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="document")


def enqueue(doc_id: int) -> None:
    executor.submit(services.process_document, doc_id)


def doc_out(d: Document, full: bool = False) -> dict:
    out = {
        "id": d.id,
        "kind": d.kind,
        "filename": d.filename,
        "mime": d.mime,
        "status": d.status,
        "error": d.error,
        "card_id": d.card_id,
        "uploaded_by": d.uploaded_by,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "doc_type": (d.result or {}).get("draft", {}).get("doc_type"),
        "row_count": len((d.result or {}).get("draft", {}).get("rows", [])),
    }
    if full:
        out["draft"] = (d.result or {}).get("draft")
        out["parsed"] = (d.result or {}).get("parsed")
    return out


def _valid_content(path: Path, mime: str) -> bool:
    """Check the content, not the declared extension/type: is it really an image or PDF that can be opened."""
    try:
        if mime == "application/pdf":
            with path.open("rb") as f:
                if f.read(5) != b"%PDF-":
                    return False
            import pypdfium2 as pdfium

            pdfium.PdfDocument(str(path)).close()
            return True
        from PIL import Image

        from .. import ai_parser  # noqa: F401  registers the HEIC opener

        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


@router.post("")
async def upload(
    files: list[UploadFile] = File(...),
    kind: str = Form("auto"),
    card_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"You can upload at most {MAX_FILES} files at once")
    waiting = db.scalar(select(func.count(Document.id)).where(Document.status.in_(["pending", "processing"])))
    if waiting + len(files) > MAX_QUEUE:
        raise HTTPException(429, "Too many documents are waiting to be read; please try again in a bit")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # 1) Stream all of them into temp files and validate; if even one is invalid, none are saved
    staged: list[tuple[UploadFile, str, Path]] = []
    total = 0
    try:
        for f in files:
            mime = f.content_type or mimetypes.guess_type(f.filename or "")[0] or ""
            if mime not in ALLOWED:
                raise HTTPException(400, f"Unsupported file type: {f.filename} ({mime or 'unknown'})")
            tmp = UPLOAD_DIR / f".{uuid.uuid4().hex}.part"
            staged.append((f, mime, tmp))
            size = 0
            with tmp.open("wb") as out:
                while chunk := await f.read(CHUNK):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(400, f"File is too large (max {MAX_BYTES // 2**20} MB): {f.filename}")
                    if total > MAX_REQUEST_BYTES:
                        raise HTTPException(400, f"Total size can be at most {MAX_REQUEST_BYTES // 2**20} MB")
                    out.write(chunk)
            if size == 0 or not _valid_content(tmp, mime):
                raise HTTPException(400, f"File couldn't be read or is corrupted: {f.filename}")
    except BaseException:
        for _, _, tmp in staged:
            tmp.unlink(missing_ok=True)
        raise

    # 2) All valid: move to permanent names, save the documents in one go, enqueue
    created = []
    for f, mime, tmp in staged:
        path = UPLOAD_DIR / f"{uuid.uuid4().hex}{ALLOWED[mime]}"
        tmp.rename(path)
        d = Document(
            kind=kind if kind in ("auto", "receipt", "statement") else "auto",
            filename=(f.filename or path.name)[:200],
            stored_path=str(path),
            mime=mime,
            card_id=card_id,
            uploaded_by=user.id,
        )
        db.add(d)
        created.append(d)
    db.commit()
    for d in created:
        enqueue(d.id)
    return [doc_out(d) for d in created]


@router.get("")
def list_documents(status: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    q = select(Document).where(Document.status != "discarded").order_by(Document.id.desc()).limit(200)
    if status:
        q = q.where(Document.status == status)
    return [doc_out(d) for d in db.scalars(q)]


@router.get("/{doc_id}")
def get_document(doc_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    return doc_out(d, full=True)


@router.get("/{doc_id}/file")
def get_file(doc_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    d = db.get(Document, doc_id)
    if not d or not Path(d.stored_path).exists():
        raise HTTPException(404)
    return FileResponse(d.stored_path, media_type=d.mime, filename=d.filename, content_disposition_type="inline")


@router.post("/{doc_id}/confirm")
def confirm(doc_id: int, body: DraftIn, db: Session = Depends(get_db), user: User = Depends(current_user)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    draft = body.draft.model_dump(mode="json")
    if draft["doc_type"] == "statement" and not draft.get("card_id"):
        raise HTTPException(400, "You must select a card for the statement")
    if not services.claim_document(db, doc_id):
        raise HTTPException(409, "This document has already been saved or is being saved right now")
    db.refresh(d)
    try:
        count = services.commit_draft(db, d, draft, user.id)
    except services.ValidationError as e:
        services.release_document(db, doc_id)
        raise HTTPException(400, str(e))
    except Exception:
        services.release_document(db, doc_id)
        raise
    return {"ok": True, "created": count}


@router.post("/{doc_id}/create-card")
def create_card(doc_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Creates a card from the statement details (if there's no matching card)."""
    d = db.get(Document, doc_id)
    if not d or d.status != "review":
        raise HTTPException(404)
    card = services.card_from_statement(db, d, user.id)
    db.commit()
    return {"card_id": card.id, "name": card.name, "draft": d.result["draft"]}


@router.post("/{doc_id}/retry")
def retry(doc_id: int, kind: str | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    if kind in ("auto", "receipt", "statement"):
        d.kind = kind
    d.status = "pending"
    d.error = ""
    db.commit()
    enqueue(d.id)
    return doc_out(d)


@router.delete("/{doc_id}")
def discard(doc_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    d = db.get(Document, doc_id)
    if not d:
        raise HTTPException(404)
    if d.status == "done":
        # Document linked to saved transactions: the file is kept, hidden from the list
        d.status = "discarded"
    else:
        Path(d.stored_path).unlink(missing_ok=True)
        db.delete(d)
    db.commit()
    return {"ok": True}

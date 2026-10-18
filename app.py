"""kindlepost: convert ebooks into Kindle-friendly files with Calibre.

The server only converts. Your phone (iOS Shortcut "Send Email" action) mails
the result to your @kindle.com address, so no email credentials live here.

POST /convert      multipart form, field "file"  -> converted file
POST /convert-url  form field "url"              -> download, converted file
GET  /             tiny upload page for a browser
GET  /health       liveness check

Both POST endpoints accept optional form fields "title", "author", and
"lookup" (0 or 1, overrides METADATA_LOOKUP for that request).
"""
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

# ---- config (env vars) -----------------------------------------------------
API_TOKEN = os.environ["API_TOKEN"]                    # shared secret for the Shortcut
NORMALIZE_EPUB = os.environ.get("NORMALIZE_EPUB", "1") == "1"
# Kindle shows PDFs as fixed pages; EPUB reflows to the screen and font size.
PDF_TO_EPUB = os.environ.get("PDF_TO_EPUB", "1") == "1"
MAX_BYTES = 48 * 1024 * 1024                           # Amazon email cap is 50 MB total
METADATA_LOOKUP = os.environ.get("METADATA_LOOKUP", "1") == "1"
# The iOS Shortcut gives up on slow requests, so the lookup gets a hard budget.
LOOKUP_TIMEOUT = int(os.environ.get("LOOKUP_TIMEOUT", "20"))

# Amazon accepts these as-is and converts them itself.
NATIVE = {".epub", ".pdf", ".doc", ".docx", ".txt", ".rtf", ".htm", ".html"}
# Everything Calibre can read that Amazon won't take -> converted to EPUB.
CONVERTIBLE = {".mobi", ".azw", ".azw3", ".azw4", ".fb2", ".lit", ".lrf", ".odt",
               ".pdb", ".pml", ".prc", ".cbz", ".cbr", ".cb7", ".chm", ".djvu",
               ".snb", ".tcr", ".txtz", ".htmlz", ".md", ".markdown", ".kepub"}

MEDIA_TYPES = {".epub": "application/epub+zip", ".pdf": "application/pdf"}
# Formats ebook-meta can write metadata into; covers only stick in EPUB.
META_WRITABLE = {".epub", ".pdf", ".docx", ".rtf"}

app = FastAPI(title="kindlepost")
log = logging.getLogger("uvicorn.error")


def check_token(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(401, "bad token")


def safe_name(name: str) -> str:
    stem, ext = os.path.splitext(os.path.basename(name or "book"))
    stem = re.sub(r"[^\w\- .(),&']", "_", stem).strip() or "book"
    return stem[:120] + ext.lower()


def ebook_convert(src: Path, dst: Path, title: str | None, author: str | None) -> None:
    cmd = ["ebook-convert", str(src), str(dst)]
    if title:
        cmd += ["--title", title]
    if author:
        cmd += ["--authors", author]
    if src.suffix.lower() in {".cbz", ".cbr", ".cb7"}:
        cmd += ["--output-profile", "kindle_pw3", "--no-process"]  # keep comic pages intact
    if dst.suffix.lower() == ".epub":
        # Plain <img> cover page instead of Calibre's SVG wrapper: Amazon's EPUB->KFX
        # conversion often renders the SVG cover page blank or drops it.
        cmd += ["--no-svg-cover", "--preserve-cover-aspect-ratio"]
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
    if r.returncode != 0 or not dst.exists():
        tail = (r.stderr or r.stdout)[-800:]
        raise HTTPException(422, f"conversion failed: {tail}")


def prepare(src: Path, title: str | None, author: str | None) -> Path:
    if src.stat().st_size == 0:
        raise HTTPException(400, "empty file")
    ext = src.suffix.lower()
    if ext == ".epub" and (NORMALIZE_EPUB or title or author):
        # Round-trip through Calibre: fixes many EPUBs Amazon rejects (E999 errors).
        out = src.parent / "out" / src.name
        out.parent.mkdir()
        ebook_convert(src, out, title, author)
        return out
    if ext == ".pdf" and PDF_TO_EPUB:
        out = src.with_suffix(".epub")
        try:
            ebook_convert(src, out, title, author)
            return out
        except HTTPException as e:
            # Scanned or damaged PDFs can't be converted but Amazon still accepts them.
            log.warning("pdf: conversion failed for %s, sending the PDF: %s", src.name, e.detail[-200:])
            out.unlink(missing_ok=True)
    if ext in NATIVE:
        return src
    if ext in CONVERTIBLE:
        out = src.with_suffix(".epub")
        ebook_convert(src, out, title, author)
        return out
    raise HTTPException(415, f"unsupported file type: {ext or 'none'}")


def calibre(cmd: list[str], timeout: int) -> subprocess.CompletedProcess | None:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None


def opf_fields(opf: str) -> dict:
    """Title, authors, and identifiers (lowercased scheme -> value) from OPF XML."""
    fields = {"title": "", "authors": [], "ids": {}}
    try:
        root = ET.fromstring(opf)
    except ET.ParseError:
        return fields
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        text = (el.text or "").strip()
        attr = {k.rsplit("}", 1)[-1]: v for k, v in el.attrib.items()}
        if tag == "title" and not fields["title"]:
            fields["title"] = text
        elif tag == "creator" and text and attr.get("role", "aut") == "aut":
            fields["authors"].append(text)
        elif tag == "identifier" and text and "scheme" in attr:
            fields["ids"][attr["scheme"].lower()] = text
    return fields


def guess_from_filename(stem: str) -> tuple[str, str]:
    """Handles "Last, First - Title", "(Series) First Last - Title", and "Title"."""
    stem = re.sub(r"^\s*[(\[][^)\]]*[)\]]\s*", "", stem.replace("_", " "))
    stem = re.sub(r"\s*[(\[][^)\]]*[)\]]\s*$", "", stem)
    if " - " not in stem:
        return stem.strip(), ""
    author, title = (s.strip() for s in stem.split(" - ", 1))
    if author.count(",") == 1:
        last, first = (s.strip() for s in author.split(","))
        author = f"{first} {last}"
    return title, author


def norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w ]", " ", s.lower()).split())


def same_book(query_title: str, query_author: str, found: dict) -> bool:
    # Subtitles vary between editions, so compare only the main title.
    a, b = norm(query_title.split(":")[0]), norm(found["title"].split(":")[0])
    if not a or not b:
        return False
    if not (a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.75):
        return False
    if not query_author:
        return True
    found_authors = norm(" ".join(found["authors"]))
    return any(len(w) > 2 and w in found_authors for w in norm(query_author).split())


def plausible(text: str) -> bool:
    """False for empty, placeholder, or file-name-like metadata such as "gs_final_v2"."""
    t = text.strip().lower()
    return (len(t) >= 3 and t != "unknown" and "_" not in t
            and not re.search(r"\.(docx?|pdf|txt|indd)\b|^microsoft word", t))


def fetch_metadata(book: Path, title: str, author: str, isbn: str, deadline: float) -> dict | None:
    """Ask Calibre's metadata sources (Amazon, Google Books, ...) for a match.

    Returns the OPF text and cover path, or None when nothing passes same_book().
    """
    remaining = int(deadline - time.monotonic())
    if remaining < 5:
        return None
    cover = book.parent / "lookup-cover.jpg"
    cover.unlink(missing_ok=True)
    cmd = ["fetch-ebook-metadata", "-o", "-d", str(remaining - 2), "-c", str(cover), "-t", title]
    if author:
        cmd += ["-a", author]
    if isbn:
        cmd += ["-i", isbn]
    r = calibre(cmd, remaining)
    m = re.search(r"<\?xml.*</package>", r.stdout, re.S) if r and r.returncode == 0 else None
    if not m:
        return None
    found = opf_fields(m.group(0))
    if not same_book(title, author, found):
        log.info("lookup: %r by %r matched %r, rejected", title, author, found["title"])
        return None
    return {"opf": m.group(0), "cover": cover if cover.exists() else None, **found}


def set_fields(book: Path, title: str | None, author: str | None) -> None:
    cmd = ["ebook-meta", str(book)]
    if title:
        cmd += ["--title", title]
    if author:
        cmd += ["--authors", author]
    calibre(cmd, 60)


def enrich(book: Path, original_stem: str, title: str | None, author: str | None) -> Path:
    """Fill in title, authors, series, identifiers (ASIN/ISBN) and cover from online sources.

    Best effort: any failure leaves the book as it was.
    """
    if book.suffix.lower() not in META_WRITABLE:
        return book
    deadline = time.monotonic() + LOOKUP_TIMEOUT
    current_opf = book.parent / "current.opf"
    r = calibre(["ebook-meta", str(book), f"--to-opf={current_opf}"], 30)
    current = opf_fields(current_opf.read_text()) if r and current_opf.exists() else opf_fields("")
    isbn = current["ids"].get("isbn", "")

    file_title, file_author = guess_from_filename(original_stem)
    cur_title = current["title"] if plausible(current["title"]) else ""
    cur_author = next((a for a in current["authors"] if plausible(a)), "")
    if title or author:
        queries = [(title or cur_title or file_title, author or cur_author or file_author)]
    else:
        queries = [(cur_title, cur_author), (file_title, file_author)]

    found = None
    for q_title, q_author in dict.fromkeys(q for q in queries if q[0]):
        found = fetch_metadata(book, q_title, q_author, isbn, deadline)
        if found:
            break
    if not found:
        log.info("lookup: no match for %s", original_stem)
        if title or author:
            set_fields(book, title, author)
        return book

    new_opf = book.parent / "found.opf"
    new_opf.write_text(found["opf"])
    cmd = ["ebook-meta", str(book), "--from-opf", str(new_opf)]
    if found["cover"] and book.suffix.lower() == ".epub":
        cmd += ["--cover", str(found["cover"])]
    # Explicit form fields beat whatever the lookup returned.
    if title:
        cmd += ["--title", title]
    if author:
        cmd += ["--authors", author]
    r = calibre(cmd, 60)
    if not r or r.returncode != 0:
        log.warning("lookup: writing metadata failed for %s", original_stem)
        return book
    log.info("lookup: %s -> %r by %s (amazon:%s)", original_stem, found["title"],
             " & ".join(found["authors"]), found["ids"].get("amazon", "-"))

    name = safe_name(f"{title or found['title']} - {author or ' & '.join(found['authors'])}{book.suffix}")
    renamed = book.with_name(name)
    if renamed != book and not renamed.exists():
        book.rename(renamed)
        return renamed
    return book


def respond(workdir: str, out: Path) -> FileResponse:
    size = out.stat().st_size
    if size > MAX_BYTES:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(413, f"{size/1e6:.1f} MB is over Amazon's email limit; "
                                 "use amazon.com/sendtokindle for big files")
    return FileResponse(out, filename=out.name,
                        media_type=MEDIA_TYPES.get(out.suffix, "application/octet-stream"),
                        background=BackgroundTask(shutil.rmtree, workdir, True))


def wants_lookup(lookup: str | None) -> bool:
    if lookup is None or lookup == "":
        return METADATA_LOOKUP
    return lookup.strip().lower() not in {"0", "false", "no", "off"}


def run(workdir: str, src: Path, title: str | None, author: str | None,
        lookup: str | None) -> FileResponse:
    try:
        out = prepare(src, title, author)
        if wants_lookup(lookup):
            try:
                out = enrich(out, src.stem, title, author)
            except Exception:
                log.exception("lookup: failed for %s, sending unchanged", src.stem)
        return respond(workdir, out)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


@app.post("/convert", dependencies=[Depends(check_token)])
def convert(file: UploadFile = File(...), title: str | None = Form(None),
            author: str | None = Form(None), lookup: str | None = Form(None)):
    d = tempfile.mkdtemp(prefix="kindlepost-")
    src = Path(d) / safe_name(file.filename)
    with src.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return run(d, src, title, author, lookup)


@app.post("/convert-url", dependencies=[Depends(check_token)])
def convert_url(url: str = Form(...), title: str | None = Form(None),
                author: str | None = Form(None), lookup: str | None = Form(None)):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    d = tempfile.mkdtemp(prefix="kindlepost-")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kindlepost"})
        with urllib.request.urlopen(req, timeout=60) as r:
            name = safe_name(r.headers.get_filename() or url.split("?")[0].rsplit("/", 1)[-1])
            src = Path(d) / name
            src.write_bytes(r.read(MAX_BYTES * 2))
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(400, f"download failed: {e}")
    return run(d, src, title, author, lookup)


@app.get("/health")
def health():
    return {"ok": shutil.which("ebook-convert") is not None}


@app.get("/", response_class=HTMLResponse)
def index():
    return """<!doctype html><meta name=viewport content="width=device-width">
<title>Kindlepost</title>
<style>body{font:16px system-ui;max-width:420px;margin:40px auto;padding:0 16px}
input,button{display:block;width:100%;margin:8px 0;padding:10px;font:inherit}</style>
<h2>Kindlepost</h2>
<form id=f><input name=file type=file required><input name=title placeholder="Title (optional)">
<input name=author placeholder="Author (optional)"><input id=t type=password placeholder="Token" required>
<button>Convert</button></form><p id=o></p>
<script>f.onsubmit=async e=>{e.preventDefault();o.textContent='Converting…';
const r=await fetch('/convert',{method:'POST',body:new FormData(f),headers:{Authorization:'Bearer '+t.value}});
if(!r.ok){o.textContent=(await r.json()).detail;return}
const name=(r.headers.get('content-disposition')||'').match(/filename\\*?=(?:utf-8'')?"?([^";]+)/i);
const a=document.createElement('a');a.href=URL.createObjectURL(await r.blob());
a.download=name?decodeURIComponent(name[1]):'book.epub';a.click();o.textContent='Done'}</script>"""

"""kindlepost: convert ebooks into Kindle-friendly files with Calibre.

The server only converts. Your phone (iOS Shortcut "Send Email" action) mails
the result to your @kindle.com address, so no email credentials live here.

POST /convert      multipart form, field "file"  -> converted file
POST /convert-url  form field "url"              -> download, converted file
GET  /             tiny upload page for a browser
GET  /health       liveness check
"""
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.background import BackgroundTask

# ---- config (env vars) -----------------------------------------------------
API_TOKEN = os.environ["API_TOKEN"]                    # shared secret for the Shortcut
NORMALIZE_EPUB = os.environ.get("NORMALIZE_EPUB", "1") == "1"
MAX_BYTES = 48 * 1024 * 1024                           # Amazon email cap is 50 MB total

# Amazon accepts these as-is and converts them itself.
NATIVE = {".epub", ".pdf", ".doc", ".docx", ".txt", ".rtf", ".htm", ".html"}
# Everything Calibre can read that Amazon won't take -> converted to EPUB.
CONVERTIBLE = {".mobi", ".azw", ".azw3", ".azw4", ".fb2", ".lit", ".lrf", ".odt",
               ".pdb", ".pml", ".prc", ".cbz", ".cbr", ".cb7", ".chm", ".djvu",
               ".snb", ".tcr", ".txtz", ".htmlz", ".md", ".markdown", ".kepub"}

MEDIA_TYPES = {".epub": "application/epub+zip", ".pdf": "application/pdf"}

app = FastAPI(title="kindlepost")


def check_token(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, API_TOKEN):
        raise HTTPException(401, "bad token")


def safe_name(name: str) -> str:
    stem, ext = os.path.splitext(os.path.basename(name or "book"))
    stem = re.sub(r"[^\w\- .()]", "_", stem).strip() or "book"
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
    if ext in NATIVE:
        return src
    if ext in CONVERTIBLE:
        out = src.with_suffix(".epub")
        ebook_convert(src, out, title, author)
        return out
    raise HTTPException(415, f"unsupported file type: {ext or 'none'}")


def respond(workdir: str, out: Path) -> FileResponse:
    size = out.stat().st_size
    if size > MAX_BYTES:
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(413, f"{size/1e6:.1f} MB is over Amazon's email limit; "
                                 "use amazon.com/sendtokindle for big files")
    return FileResponse(out, filename=out.name,
                        media_type=MEDIA_TYPES.get(out.suffix, "application/octet-stream"),
                        background=BackgroundTask(shutil.rmtree, workdir, True))


def run(workdir: str, src: Path, title: str | None, author: str | None) -> FileResponse:
    try:
        return respond(workdir, prepare(src, title, author))
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


@app.post("/convert", dependencies=[Depends(check_token)])
def convert(file: UploadFile = File(...), title: str | None = Form(None),
            author: str | None = Form(None)):
    d = tempfile.mkdtemp(prefix="kindlepost-")
    src = Path(d) / safe_name(file.filename)
    with src.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return run(d, src, title, author)


@app.post("/convert-url", dependencies=[Depends(check_token)])
def convert_url(url: str = Form(...), title: str | None = Form(None),
                author: str | None = Form(None)):
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
    return run(d, src, title, author)


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

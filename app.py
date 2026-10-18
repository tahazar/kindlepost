"""kindlepost: convert ebooks with Calibre and email them to a Kindle.

POST /send      multipart form, field "file"   -> convert + email
POST /send-url  form or JSON, field "url"      -> download, convert + email
GET  /          tiny upload page for a browser
GET  /health    liveness check
"""
import os
import re
import secrets
import shutil
import smtplib
import subprocess
import tempfile
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

# ---- config (env vars) -----------------------------------------------------
API_TOKEN = os.environ["API_TOKEN"]                    # shared secret for the Shortcut
KINDLE_EMAIL = os.environ["KINDLE_EMAIL"]              # you@kindle.com
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]                    # must be on Amazon's approved list
SMTP_PASS = os.environ["SMTP_PASS"]                    # Gmail: use an app password
FROM_ADDR = os.environ.get("FROM_ADDR", SMTP_USER)
NORMALIZE_EPUB = os.environ.get("NORMALIZE_EPUB", "1") == "1"
MAX_BYTES = 48 * 1024 * 1024                           # Amazon email cap is 50 MB total

# Amazon accepts these as-is and converts them itself.
NATIVE = {".epub", ".pdf", ".doc", ".docx", ".txt", ".rtf", ".htm", ".html"}
# Everything Calibre can read that Amazon won't take -> converted to EPUB.
CONVERTIBLE = {".mobi", ".azw", ".azw3", ".azw4", ".fb2", ".lit", ".lrf", ".odt",
               ".pdb", ".pml", ".prc", ".cbz", ".cbr", ".cb7", ".chm", ".djvu",
               ".snb", ".tcr", ".txtz", ".htmlz", ".md", ".markdown", ".kepub"}

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
    ext = src.suffix.lower()
    if ext == ".epub" and (NORMALIZE_EPUB or title or author):
        # Round-trip through Calibre: fixes many EPUBs Amazon rejects (E999 errors).
        out = src.with_name(src.stem + ".fixed.epub")
        ebook_convert(src, out, title, author)
        return out.rename(src.with_name(src.stem + ".kindle.epub"))
    if ext in NATIVE:
        return src
    if ext in CONVERTIBLE:
        out = src.with_suffix(".epub")
        ebook_convert(src, out, title, author)
        return out
    raise HTTPException(415, f"unsupported file type: {ext or 'none'}")


def email_to_kindle(path: Path, to: str) -> None:
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise HTTPException(413, f"{size/1e6:.1f} MB is over Amazon's email limit; "
                                 "use amazon.com/sendtokindle for big files")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = FROM_ADDR, to, path.stem
    msg.set_content("Sent by kindlepost")
    msg.add_attachment(path.read_bytes(), maintype="application",
                       subtype="octet-stream", filename=path.name.replace(".kindle", ""))
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60) as s:
        s.starttls()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


def process(src: Path, title: str | None, author: str | None, to: str | None) -> dict:
    if src.stat().st_size == 0:
        raise HTTPException(400, "empty file")
    out = prepare(src, title, author)
    email_to_kindle(out, to or KINDLE_EMAIL)
    return {"ok": True, "sent": out.name.replace(".kindle", ""),
            "message": f"Sent {out.stem.replace('.kindle', '')} to your Kindle"}


@app.post("/send", dependencies=[Depends(check_token)])
async def send(file: UploadFile = File(...), title: str | None = Form(None),
               author: str | None = Form(None), to: str | None = Form(None)):
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / safe_name(file.filename)
        with src.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        return process(src, title, author, to)


@app.post("/send-url", dependencies=[Depends(check_token)])
async def send_url(url: str = Form(...), title: str | None = Form(None),
                   author: str | None = Form(None), to: str | None = Form(None)):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    with tempfile.TemporaryDirectory() as d:
        req = urllib.request.Request(url, headers={"User-Agent": "kindlepost"})
        with urllib.request.urlopen(req, timeout=60) as r:
            name = safe_name(r.headers.get_filename() or url.split("?")[0].rsplit("/", 1)[-1])
            src = Path(d) / name
            src.write_bytes(r.read(MAX_BYTES * 2))
        return process(src, title, author, to)


@app.get("/health")
def health():
    return {"ok": shutil.which("ebook-convert") is not None}


@app.get("/", response_class=HTMLResponse)
def index():
    return """<!doctype html><meta name=viewport content="width=device-width">
<title>Kindlepost</title>
<style>body{font:16px system-ui;max-width:420px;margin:40px auto;padding:0 16px}
input,button{display:block;width:100%;margin:8px 0;padding:10px;font:inherit}</style>
<h2>Send to Kindle</h2>
<form id=f><input name=file type=file required><input name=title placeholder="Title (optional)">
<input name=author placeholder="Author (optional)"><input id=t type=password placeholder="Token" required>
<button>Send</button></form><p id=o></p>
<script>f.onsubmit=async e=>{e.preventDefault();o.textContent='Converting…';
const r=await fetch('/send',{method:'POST',body:new FormData(f),headers:{Authorization:'Bearer '+t.value}});
const j=await r.json();o.textContent=j.message||j.detail}</script>"""

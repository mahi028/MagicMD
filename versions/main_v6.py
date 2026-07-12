from flask import Flask, request, jsonify, Response, send_file
import json
from pathlib import Path
import markdown
import html
import re
import os
import uuid
import mimetypes
import io
from pathlib import Path
import yaml

# ============================================================
# CONFIG
# ============================================================

CONFIG_PATH = Path(__file__).parent / "config.yaml"

with open(CONFIG_PATH, "r") as f:
    cfg = yaml.safe_load(f)

paths = cfg["paths"]
app = cfg["app_config"]

MARKDOWN_FOLDER = paths["markdown_folder"]
ARTIFACT_DIR = paths["artifact_dir"]

HOST = app["host"]
PORT = app["port"]
MAX_UPLOAD_BYTES = app["max_upload_bytes"]

# ============================================================
# SETUP
# ============================================================

app          = Flask(__name__)
base_path    = Path(MARKDOWN_FOLDER).resolve()
artifact_path = Path(ARTIFACT_DIR).resolve()
artifact_path.mkdir(parents=True, exist_ok=True)

try:
    from xhtml2pdf import pisa
    PDF_EXPORT_AVAILABLE = True
except ImportError:
    PDF_EXPORT_AVAILABLE = False


# ============================================================
# HELPERS
# ============================================================

def get_markdown_files():
    files = []
    for path in base_path.rglob("*.md"):
        if ".ipynb_checkpoints" in path.parts:
            continue
        rel = path.relative_to(base_path)
        files.append(str(rel).replace("\\", "/"))
    return sorted(files)


def get_all_top_level_folders():
    """Return every top-level directory under base_path, regardless of
    whether it currently contains any markdown files. This lets empty
    folders show up in the sidebar tree."""
    folders = []
    try:
        for entry in base_path.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if entry.name == "__pycache__":
                continue
            folders.append(entry.name)
    except FileNotFoundError:
        pass
    return folders


def resolve_safe_path(relative_path):
    relative_path = relative_path.split("?")[0].split("#")[0]
    safe = (base_path / relative_path).resolve()
    if not str(safe).startswith(str(base_path)):
        return None
    return safe


def resolve_safe_md_path(relative_path):
    if not relative_path.lower().endswith(".md"):
        return None
    return resolve_safe_path(relative_path)


def read_markdown(relative_path):
    safe = resolve_safe_md_path(relative_path)
    if safe is None or not safe.exists():
        return None
    return safe.read_text(encoding="utf-8")


def write_markdown(relative_path, content):
    safe = resolve_safe_md_path(relative_path)
    if safe is None:
        return False
    safe.parent.mkdir(parents=True, exist_ok=True)
    safe.write_text(content, encoding="utf-8")
    return True


def get_readme():
    for f in base_path.iterdir():
        if f.name.lower() == "readme.md":
            return f.name
    return None


def markdown_to_plaintext(md_text):
    text = md_text
    text = re.sub(r"```mermaid\s*\n.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"```[a-zA-Z0-9_+-]*\n(.*?)```", lambda m: m.group(1), text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
    def media_tag_replacer(m):
        src = re.search(r'src="([^"]+)"', m.group(0))
        return f"[media: {src.group(1) if src else ''}]"
    text = re.sub(r"<video[^>]*>.*?</video>", media_tag_replacer, text, flags=re.DOTALL)
    text = re.sub(r"<audio[^>]*>.*?</audio>", media_tag_replacer, text, flags=re.DOTALL)
    text = re.sub(r"<video[^>]*/?>", media_tag_replacer, text)
    text = re.sub(r"<audio[^>]*/?>", media_tag_replacer, text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"(\*\*\*|___)(.+?)\1", r"\2", text)
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.+?)\1", r"\2", text)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*\|\s*", "    ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def render_markdown(md_text):
    stash = {}
    counter = [0]

    def s(expr):
        key = f"MATHSTASH{counter[0]}END"
        stash[key] = expr
        counter[0] += 1
        return key

    md_text = re.sub(
        r"\$\$(.+?)\$\$",
        lambda m: s(f'<span class="math-display">\\({m.group(1).strip()}\\)</span>'),
        md_text, flags=re.DOTALL,
    )
    md_text = re.sub(
        r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
        lambda m: s(f'<span class="math-inline">\\({m.group(1).strip()}\\)</span>'),
        md_text,
    )
    md_text = re.sub(
        r"```mermaid\s*\n(.*?)```",
        lambda m: f'\n<div class="mermaid">\n{html.escape(m.group(1).strip())}\n</div>\n',
        md_text, flags=re.DOTALL,
    )

    result = markdown.markdown(md_text, extensions=["fenced_code", "tables", "toc", "nl2br"])

    for key, val in stash.items():
        result = result.replace(f"<p>{key}</p>", val).replace(key, val)

    return result


# ============================================================
# PWA ROUTES
# ============================================================

@app.route("/manifest.json")
def manifest():
    data = {
        "name": "Markdown Library",
        "short_name": "MdLib",
        "description": "Read and edit your markdown notes",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0c1220",
        "theme_color": "#0c1220",
        "orientation": "any",
        "icons": [
            {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
        ],
    }
    return Response(json.dumps(data), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    sw = r"""
const CACHE = 'mdlib-v1';
const SHELL = [
  '/',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
  'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/artifacts/')) return;
  e.respondWith(caches.match(e.request).then(cached => cached || fetch(e.request)));
});
"""
    return Response(sw, mimetype="application/javascript",
                    headers={"Service-Worker-Allowed": "/"})


# ============================================================
# API ROUTES
# ============================================================

@app.route("/")
def index():
    file_param = request.args.get("file", "").strip()
    if file_param and not file_param.lower().endswith(".md"):
        return Response("Only .md files are allowed.", status=400, mimetype="text/plain")
    readme = get_readme()
    initial_file = json.dumps(file_param or readme or "")
    return Response(HTML.replace("__INITIAL_FILE__", initial_file), mimetype="text/html; charset=utf-8")


@app.route("/favicon.svg")
def favicon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <rect width="32" height="32" rx="7" fill="#1e293b"/>
  <text x="5" y="23" font-family="Georgia, serif" font-size="22" font-weight="bold" fill="#60a5fa">M</text>
  <rect x="22" y="14" width="6" height="2.5" rx="1.2" fill="#60a5fa"/>
  <rect x="22" y="19" width="6" height="2.5" rx="1.2" fill="#94a3b8"/>
</svg>"""
    return Response(svg, mimetype="image/svg+xml")


@app.route("/api/files")
def api_files():
    flat = get_markdown_files()
    root_files, folder_map = [], {}
    for f in flat:
        parts = f.split("/")
        if len(parts) == 1:
            root_files.append(f)
        else:
            folder_map.setdefault(parts[0], []).append(f)
    # Make sure folders that currently have no markdown files inside
    # them still show up in the tree.
    for name in get_all_top_level_folders():
        folder_map.setdefault(name, [])
    folders = [{"name": n, "files": fs} for n, fs in sorted(folder_map.items())]
    return jsonify({"root_files": root_files, "folders": folders})


@app.route("/api/readme")
def api_readme():
    return jsonify({"readme": get_readme()})


@app.route("/api/read")
def api_read():
    file = request.args.get("file", "").strip()
    if not file.lower().endswith(".md"):
        return Response("Only .md files are allowed.", status=400)
    md_text = read_markdown(file)
    if md_text is None:
        return Response("Not found", status=404)
    return Response(md_text, mimetype="text/plain; charset=utf-8")


@app.route("/api/render", methods=["POST"])
def api_render():
    data = request.get_json(force=True)
    return Response(render_markdown(data.get("markdown", "")), mimetype="text/html; charset=utf-8")


@app.route("/api/save", methods=["POST"])
def api_save():
    data = request.get_json(force=True)
    file = data.get("file", "").strip()
    content = data.get("content", "")
    if not file:
        return jsonify({"ok": False, "error": "No file specified"}), 400
    if not file.lower().endswith(".md"):
        return jsonify({"ok": False, "error": "Only .md files are allowed"}), 400
    if not write_markdown(file, content):
        return jsonify({"ok": False, "error": "Path traversal blocked"}), 403
    return jsonify({"ok": True})


@app.route("/api/export/txt", methods=["POST"])
def api_export_txt():
    data = request.get_json(force=True)
    plain = markdown_to_plaintext(data.get("markdown", ""))
    filename = data.get("filename", "document")
    buf = io.BytesIO(plain.encode("utf-8"))
    buf.seek(0)
    return send_file(buf, mimetype="text/plain", as_attachment=True, download_name=f"{filename}.txt")


@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    if not PDF_EXPORT_AVAILABLE:
        return jsonify({"ok": False, "error": "PDF export unavailable. Install with: pip install xhtml2pdf"}), 501
    data = request.get_json(force=True)
    rendered_html = data.get("html", "")
    filename = data.get("filename", "document")
    if not rendered_html.strip():
        return jsonify({"ok": False, "error": "No content to export"}), 400
    pdf_buffer = io.BytesIO()
    try:
        result = pisa.CreatePDF(src=rendered_html, dest=pdf_buffer, encoding="utf-8")
    except Exception as e:
        return jsonify({"ok": False, "error": f"PDF generation failed: {e}"}), 500
    if result.err:
        return jsonify({"ok": False, "error": "PDF generation encountered errors"}), 500
    pdf_buffer.seek(0)
    return send_file(pdf_buffer, mimetype="application/pdf", as_attachment=True, download_name=f"{filename}.pdf")


@app.route("/api/new", methods=["POST"])
def api_new():
    data = request.get_json(force=True)
    file = data.get("file", "").strip()
    if not file:
        return jsonify({"ok": False, "error": "No filename"}), 400
    if not file.endswith(".md"):
        file += ".md"
    safe = resolve_safe_md_path(file)
    if safe is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 403
    if safe.exists():
        return jsonify({"ok": False, "error": "File already exists"}), 409
    safe.parent.mkdir(parents=True, exist_ok=True)
    safe.write_text(f"# {Path(file).stem}\n\n", encoding="utf-8")
    return jsonify({"ok": True, "file": file})


@app.route("/api/mkdir", methods=["POST"])
def api_mkdir():
    data = request.get_json(force=True)
    folder = data.get("folder", "").strip()
    if not folder:
        return jsonify({"ok": False, "error": "No folder name"}), 400
    folder = folder.strip("/")
    safe = resolve_safe_path(folder)
    if safe is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 403
    if safe.exists():
        return jsonify({"ok": False, "error": "Folder already exists"}), 409
    safe.mkdir(parents=True, exist_ok=False)
    return jsonify({"ok": True, "folder": folder})


@app.route("/api/rename", methods=["POST"])
def api_rename():
    data = request.get_json(force=True)
    old_path = data.get("old", "").strip()
    new_path = data.get("new", "").strip()
    if not old_path or not new_path:
        return jsonify({"ok": False, "error": "Missing old or new path"}), 400

    old_safe = resolve_safe_path(old_path)
    new_safe = resolve_safe_path(new_path)
    if old_safe is None or new_safe is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 403
    if not old_safe.exists():
        return jsonify({"ok": False, "error": "Source does not exist"}), 404
    if new_safe.exists():
        return jsonify({"ok": False, "error": "Destination already exists"}), 409

    new_safe.parent.mkdir(parents=True, exist_ok=True)
    old_safe.rename(new_safe)
    return jsonify({"ok": True})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "Empty filename"}), 400
    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": "File exceeds 1 GB limit"}), 413
    ext = Path(f.filename).suffix.lower() or ""
    safe_name = uuid.uuid4().hex + ext
    (artifact_path / safe_name).write_bytes(data)
    return jsonify({"ok": True, "filename": safe_name, "original": f.filename})


@app.route("/api/upload-md", methods=["POST"])
def api_upload_md():
    """Upload a .md file directly into the library tree."""
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part"}), 400
    f = request.files["file"]
    dest_folder = request.form.get("folder", "").strip().strip("/")
    if not f.filename:
        return jsonify({"ok": False, "error": "Empty filename"}), 400
    if not f.filename.lower().endswith(".md"):
        return jsonify({"ok": False, "error": "Only .md files allowed"}), 400

    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": "File too large"}), 413

    safe_filename = re.sub(r"[^\w\-. ]", "_", f.filename)
    rel = (dest_folder + "/" + safe_filename) if dest_folder else safe_filename
    safe = resolve_safe_md_path(rel)
    if safe is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 403
    if safe.exists():
        stem = Path(safe_filename).stem
        rel = (dest_folder + "/" + stem + "_" + uuid.uuid4().hex[:6] + ".md") if dest_folder else (stem + "_" + uuid.uuid4().hex[:6] + ".md")
        safe = resolve_safe_md_path(rel)

    safe.parent.mkdir(parents=True, exist_ok=True)
    safe.write_bytes(data)
    return jsonify({"ok": True, "file": rel})


@app.route("/artifacts/<path:filename>")
def serve_artifact(filename):
    if not re.match(r'^[a-f0-9]+\.[a-z0-9]+$', filename):
        return Response("Forbidden", status=403)
    dest = artifact_path / filename
    if not dest.exists():
        return Response("Not found", status=404)
    mime, _ = mimetypes.guess_type(str(dest))
    return Response(dest.read_bytes(), mimetype=mime or "application/octet-stream")


# ============================================================
# FRONTEND
# ============================================================

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Markdown Library</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0c1220" id="themeColorMeta">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">

<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">
<link id="hlDark"  rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<link id="hlLight" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css" disabled>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
<script>
MathJax = {
  tex: { inlineMath: [['\\(','\\)']], displayMath: [['\\[','\\]']] },
  options: { skipHtmlTags: ['script','noscript','style','textarea','pre'] },
  startup: { typeset: false }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>

<style>
/* ── Tokens: dark (default) ── */
:root {
  --bg:          #0c1220;
  --panel:       #111827;
  --panel2:      #1a2538;
  --article-bg:  #111827;
  --border:      #1e3148;
  --border-soft: rgba(30,49,72,.5);
  --text:        #e2e8f0;
  --muted:       #7e9ab5;
  --body:        #c8d4e3;
  --accent:      #3b82f6;
  --accent-h:    #60a5fa;
  --accent-dim:  rgba(59,130,246,.12);
  --danger:      #ef4444;
  --success:     #22c55e;
  --warn:        #f59e0b;
  --code-bg:     #0a1120;
  --sidebar-w:   272px;
  --topbar-h:    48px;
  --scrollbar:   #1f3a56;
  --r-sm: 5px; --r-md: 8px; --r-lg: 12px;
  --shadow: 0 8px 32px rgba(0,0,0,.35);
}
[data-theme="light"] {
  --bg:         #f0f4f8;
  --panel:      #ffffff;
  --panel2:     #eaeff7;
  --article-bg: #ffffff;
  --border:     #d0dbe8;
  --border-soft:rgba(200,215,230,.6);
  --text:       #0f172a;
  --muted:      #5a7490;
  --body:       #334155;
  --accent:     #2563eb;
  --accent-h:   #1d4ed8;
  --accent-dim: rgba(37,99,235,.08);
  --code-bg:    #f1f5f9;
  --scrollbar:  #b0c2d6;
  --shadow:     0 4px 20px rgba(0,0,0,.1);
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body { height: 100%; overflow: hidden; background: var(--bg); color: var(--text);
  font-family: 'Inter','Segoe UI',system-ui,sans-serif; font-size: 14px; line-height: 1.5; }

/* ── Layout shell ── */
#app     { display: flex; height: 100vh; }
#sidebar { width: var(--sidebar-w); min-width: var(--sidebar-w); background: var(--panel);
  border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 10;
  transition: width .22s cubic-bezier(.4,0,.2,1), min-width .22s cubic-bezier(.4,0,.2,1),
              transform .22s cubic-bezier(.4,0,.2,1); overflow: hidden; }
#sidebar.collapsed { width: 0; min-width: 0; border-right-color: transparent; }
#main    { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

/* Mobile: slide-over */
@media (max-width: 767px) {
  #sidebar { position: fixed; inset: 0 auto 0 0; width: var(--sidebar-w) !important;
    min-width: var(--sidebar-w) !important; transform: translateX(-100%);
    transition: transform .22s cubic-bezier(.4,0,.2,1); box-shadow: none; }
  #sidebar.show { transform: translateX(0); box-shadow: 8px 0 40px rgba(0,0,0,.4); }
  #sidebar.collapsed { transform: translateX(-100%); }
  #backdrop { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 9; }
  #backdrop.show { display: block; }
}

/* ── Sidebar inner ── */
.sb-head { padding: 10px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
.sb-brand { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700;
  color: var(--text); padding: 2px 0 10px; white-space: nowrap; }

#search { width: 100%; background: var(--panel2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-sm); padding: 6px 10px 6px 30px; font-size: 12.5px; outline: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='%237e9ab5' stroke-width='2'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='m21 21-4.35-4.35'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: 9px center;
  transition: border-color .15s; }
#search:focus { border-color: var(--accent); }
#search::placeholder { color: var(--muted); }

/* ── Sidebar action bar ── */
.sb-actions { display: flex; gap: 5px; margin-top: 8px; align-items: center; }

.btn-sb { flex: 1; padding: 5px 7px; font-size: 12px; font-weight: 600; border-radius: var(--r-sm);
  cursor: pointer; border: 1px solid var(--border); background: var(--panel2); color: var(--muted);
  display: flex; align-items: center; justify-content: center; gap: 4px; white-space: nowrap;
  transition: background .12s, color .12s, border-color .12s; }
.btn-sb:hover { background: var(--panel); border-color: var(--accent); color: var(--accent); }
.btn-sb.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-sb.primary:hover { background: var(--accent-h); }
.btn-sb.icon-only { flex: 0 0 28px; padding: 5px; font-size: 13px; }

/* Context hint strip */
#ctxHint { font-size: 10.5px; color: var(--muted); padding: 4px 10px 0;
  display: none; align-items: center; gap: 5px; overflow: hidden; white-space: nowrap; }
#ctxHint.show { display: flex; }
#ctxHint i { font-size: 9px; opacity: .6; }
#ctxHint span { overflow: hidden; text-overflow: ellipsis; }

#fileList { flex: 1; overflow-y: auto; padding: 4px 0; }

/* ── Tree rows ── */
.folder-row {
  padding: 6px 10px;
  cursor: pointer;
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: .08em;
  display: flex;
  align-items: center;
  gap: 6px;
  user-select: none;
  /* Only transition background and color — never height/padding */
  transition: background .12s, color .12s;
  position: relative;
}
.folder-row:hover { background: var(--panel2); color: var(--text); }
.folder-row.selected { background: var(--accent-dim); color: var(--text); }

/* Chevron rotates via JS transform — CSS transition handles the animation */
.folder-chevron {
  font-size: 9px;
  flex-shrink: 0;
  transition: transform .2s cubic-bezier(.4,0,.2,1);
  display: inline-block;
}
.folder-row.open .folder-chevron { transform: rotate(90deg); }

.folder-count { margin-left: auto; font-size: 10px; font-weight: 400; opacity: .5; flex-shrink: 0; }

/* folder-children: no CSS transition — height animated via JS */
.folder-children { overflow: hidden; }

/* Row action buttons */
.row-actions { display: flex; align-items: center; gap: 2px; margin-left: 4px;
  opacity: 0; transition: opacity .1s; }
.folder-row:hover .row-actions,
.file-item:hover .row-actions,
.folder-row.selected .row-actions { opacity: 1; }
.row-btn { width: 20px; height: 20px; display: flex; align-items: center; justify-content: center;
  border-radius: 4px; border: none; background: transparent; color: var(--muted); cursor: pointer;
  font-size: 11px; flex-shrink: 0; transition: background .1s, color .1s; padding: 0; }
.row-btn:hover { background: var(--panel2); color: var(--accent); }

.file-item { padding: 6px 10px 6px 26px; cursor: pointer; font-size: 12.5px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  border-left: 2px solid transparent; border-bottom: 1px solid var(--border-soft);
  transition: background .1s, color .1s, border-left-color .1s;
  display: flex; align-items: center; gap: 0; }
.file-item .fi-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-item.root { padding-left: 10px; }
.file-item:hover { background: var(--panel2); color: var(--text); }
.file-item.active { background: var(--accent-dim); border-left-color: var(--accent); color: var(--text); font-weight: 600; }
.file-item.readme { color: var(--accent); }
.folder-tag { font-size: 10px; color: var(--muted); opacity: .6; margin-right: 3px; flex-shrink: 0; }

.folder-empty-hint { padding: 5px 10px 8px 30px; font-size: 11px; color: var(--muted); opacity: .6;
  font-style: italic; }

/* ── Topbar ── */
#topbar { height: var(--topbar-h); background: var(--panel); border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 6px; padding: 0 10px; flex-shrink: 0; }
#title { flex: 1; font-size: 12.5px; color: var(--muted); white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; min-width: 0; }
#title .name { color: var(--text); font-weight: 600; font-size: 13px; }

.icon-btn { width: 30px; height: 30px; display: flex; align-items: center; justify-content: center;
  border-radius: var(--r-sm); border: 1px solid var(--border); background: var(--panel2);
  color: var(--muted); cursor: pointer; font-size: 14px; flex-shrink: 0;
  transition: background .12s, color .12s, border-color .12s; }
.icon-btn:hover { background: var(--panel); color: var(--accent); border-color: var(--accent); }

.tab-group { display: flex; border: 1px solid var(--border); border-radius: var(--r-sm); overflow: hidden; }
.tab-btn { padding: 5px 11px; font-size: 12px; font-weight: 600; cursor: pointer;
  background: var(--panel2); color: var(--muted); border: none; outline: none;
  transition: background .12s, color .12s; }
.tab-btn + .tab-btn { border-left: 1px solid var(--border); }
.tab-btn.active { background: var(--accent); color: #fff; }
.tab-btn:hover:not(.active) { background: var(--panel); color: var(--text); }

#saveBtn { padding: 5px 11px; font-size: 12px; font-weight: 600; background: var(--success);
  color: #fff; border: none; border-radius: var(--r-sm); cursor: pointer;
  display: none; align-items: center; gap: 5px; transition: filter .12s; }
#saveBtn:hover { filter: brightness(1.1); }

#saveStatus { font-size: 11px; white-space: nowrap; font-weight: 600; }
#saveStatus.saved   { color: var(--success); }
#saveStatus.unsaved { color: var(--warn); }
#saveStatus.error   { color: var(--danger); }

/* ── Download dropdown ── */
.dd-wrap { position: relative; }
.dd-menu { display: none; position: absolute; top: calc(100% + 5px); right: 0;
  background: var(--panel); border: 1px solid var(--border); border-radius: var(--r-md);
  box-shadow: var(--shadow); min-width: 180px; padding: 4px; z-index: 100; }
.dd-menu.show { display: block; }
.dd-item { display: flex; align-items: center; gap: 8px; padding: 7px 9px; font-size: 12.5px;
  font-weight: 500; color: var(--text); border-radius: var(--r-sm); cursor: pointer;
  transition: background .1s; white-space: nowrap; }
.dd-item:hover { background: var(--panel2); }
.dd-item i { font-size: 13px; color: var(--muted); width: 14px; text-align: center; }
.dd-item .ext { margin-left: auto; font-size: 10px; color: var(--muted);
  font-family: 'JetBrains Mono',Consolas,monospace; }

/* ── Workspace ── */
#workspace { flex: 1; display: flex; overflow: hidden; }
#previewWrap { flex: 1; display: flex; overflow: hidden; }
#previewRendered { flex: 1; overflow-y: auto; padding: 20px; background: var(--bg); }
#editorWrap { flex: 1; display: flex; overflow: hidden; }
#editorPane { flex: 0 0 50%; display: flex; flex-direction: column; overflow: hidden; position: relative;
  min-width: 0; }
#editorRenderedPane { flex: 1 1 auto; overflow-y: auto; padding: 20px; background: var(--bg); min-width: 0; }

/* ── Draggable split resizer ── */
.split-resizer {
  flex: 0 0 6px;
  width: 6px;
  cursor: col-resize;
  background: var(--border);
  position: relative;
  z-index: 5;
  transition: background .12s;
}
.split-resizer::after {
  content: '';
  position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  width: 2px; height: 28px; border-radius: 2px;
  background: var(--muted); opacity: .5;
}
.split-resizer:hover, .split-resizer.dragging { background: var(--accent); }
.split-resizer:hover::after, .split-resizer.dragging::after { opacity: 0; }

@media (max-width: 767px) {
  #editorWrap { flex-direction: column; }
  #editorPane { flex: 0 0 50%; border-right: none; }
  #editorRenderedPane { flex: 1 1 auto; }
  .split-resizer { flex: 0 0 6px; width: 100%; height: 6px; cursor: row-resize; }
  .split-resizer::after { width: 28px; height: 2px; }
}

/* ── Article prose ── */
.article { max-width: 800px; margin: 0 auto; background: var(--article-bg);
  border-radius: var(--r-lg); padding: 2.25rem 2.75rem;
  border: 1px solid var(--border); box-shadow: var(--shadow); }
@media (max-width: 575px) {
  #previewRendered, #editorRenderedPane { padding: .75rem; }
  .article { padding: 1.25rem 1rem; }
}

.pane-label { padding: 5px 10px; font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 1.2px; color: var(--muted); background: var(--panel);
  border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }

#editor { flex: 1; resize: none; background: var(--code-bg); color: var(--text);
  font-family: 'JetBrains Mono',Consolas,'Fira Code',monospace; font-size: 13px;
  line-height: 1.75; padding: 1rem 1.25rem; border: none; outline: none; tab-size: 4; }

/* Drop overlay */
#dropOverlay { display: none; position: absolute; inset: 0; z-index: 50;
  background: rgba(59,130,246,.12); border: 2px dashed var(--accent); border-radius: var(--r-md);
  align-items: center; justify-content: center; flex-direction: column; gap: 8px;
  color: var(--accent); font-weight: 600; font-size: 14px; pointer-events: none; }
#dropOverlay.active { display: flex; }
#dropOverlay i { font-size: 2.2rem; }

/* Upload toast */
#toast { position: fixed; bottom: 20px; right: 20px; z-index: 200; background: var(--panel);
  border: 1px solid var(--border); border-radius: var(--r-md); padding: 9px 13px;
  font-size: 12.5px; color: var(--text); box-shadow: var(--shadow);
  display: none; align-items: center; gap: 10px; min-width: 200px; }
#toast.show { display: flex; }
.toast-bar { flex: 1; height: 3px; background: var(--panel2); border-radius: 999px; overflow: hidden; }
.toast-fill { height: 100%; background: var(--accent); width: 0; transition: width .2s; border-radius: 999px; }

/* Article typography */
.article h1, .article h2, .article h3, .article h4 { color: var(--text); font-weight: 700; }
.article h1 { font-size: 1.8rem; margin-bottom: .3em; line-height: 1.25; }
.article h2 { font-size: 1.3rem; margin: 1.4em 0 .3em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
.article h3 { font-size: 1.05rem; margin: 1em 0 .2em; }
.article p, .article li { line-height: 1.85; color: var(--body); margin-bottom: .45em; }
.article code { font-family: 'JetBrains Mono',Consolas,monospace; font-size: .83em;
  background: var(--panel2); color: var(--accent-h); padding: .12em .38em;
  border-radius: 4px; border: 1px solid var(--border); }
.article pre { border-radius: var(--r-md); overflow: auto; padding: 13px;
  background: var(--code-bg) !important; margin: .9em 0;
  border: 1px solid var(--border); }
.article pre code { background: transparent; border: none; padding: 0; color: inherit; }
.article blockquote { border-left: 3px solid var(--accent); padding: .45em .9em;
  color: var(--muted); margin: .9em 0; background: var(--panel2);
  border-radius: 0 var(--r-sm) var(--r-sm) 0; }
.article table { width: 100%; border-collapse: collapse; margin: .9em 0; font-size: .92em; }
.article th, .article td { border: 1px solid var(--border); padding: 7px 11px; }
.article th { background: var(--panel2); font-weight: 600; }
.article tr:nth-child(even) td { background: rgba(128,128,128,.03); }
.article a { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.article a:hover { color: var(--accent-h); }
.article img { max-width: 100%; border-radius: var(--r-md); display: block; margin: .9em auto; }
.article hr { border: none; border-top: 1px solid var(--border); margin: 1.8em 0; }
.article video, .article audio { display: block; max-width: 100%; margin: .9em auto;
  border-radius: var(--r-md); border: 1px solid var(--border); }
.math-display { display: block; text-align: center; margin: 1.3em 0; overflow-x: auto; }
.mermaid { border-radius: var(--r-md); padding: 14px; overflow: auto; margin: 1em 0;
  background: #1e1e2e; border: 1px solid var(--border); }
[data-theme="light"] .mermaid { background: #f8fafc; }

.empty { text-align: center; color: var(--muted); padding: 5rem 0; }
.empty i { font-size: 2.2rem; display: block; margin-bottom: 10px; opacity: .3; }
.empty p { font-size: 13px; margin-top: 4px; }

.article-footer { margin-top: 2.5rem; padding-top: .9rem; border-top: 1px solid var(--border);
  font-size: 11px; color: var(--muted); display: flex; justify-content: space-between; align-items: center; }

/* Scrollbar */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 999px; }
::-webkit-scrollbar-track { background: transparent; }

/* ── Modals ── */
.modal-bg { position: fixed; inset: 0; z-index: 1000; display: none; align-items: center;
  justify-content: center; background: rgba(0,0,0,.5); backdrop-filter: blur(2px); }
.modal-bg.show { display: flex; }
.modal-box { background: var(--panel); border: 1px solid var(--border); border-radius: var(--r-lg);
  padding: 20px; width: 420px; max-width: 92vw; box-shadow: var(--shadow); }
.modal-box h5 { font-size: 14px; font-weight: 700; margin-bottom: 12px;
  display: flex; align-items: center; gap: 8px; }
.modal-box h5 i { color: var(--accent); }

.modal-type-row { display: flex; gap: 8px; margin-bottom: 14px; }
.modal-type-btn { flex: 1; padding: 9px 12px; border-radius: var(--r-sm); border: 1px solid var(--border);
  background: var(--panel2); color: var(--muted); cursor: pointer; font-size: 12.5px; font-weight: 600;
  display: flex; align-items: center; gap: 7px; transition: all .12s; }
.modal-type-btn:hover { border-color: var(--accent); color: var(--text); }
.modal-type-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--accent); }
.modal-type-btn i { font-size: 15px; }

.modal-ctx { font-size: 11.5px; color: var(--muted); background: var(--panel2);
  border: 1px solid var(--border); border-radius: var(--r-sm); padding: 6px 10px;
  margin-bottom: 12px; display: flex; align-items: center; gap: 6px; }
.modal-ctx i { font-size: 11px; opacity: .6; }

.modal-input { width: 100%; background: var(--panel2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-sm); padding: 7px 11px; font-size: 13px; outline: none;
  transition: border-color .15s; }
.modal-input:focus { border-color: var(--accent); }
.modal-hint { font-size: 11.5px; color: var(--muted); margin-top: 5px; }
.modal-error { font-size: 12px; color: var(--danger); margin-top: 5px; display: none; }
.modal-footer { display: flex; justify-content: flex-end; gap: 7px; margin-top: 14px; }
.btn-modal { padding: 6px 14px; font-size: 13px; font-weight: 600; border-radius: var(--r-sm);
  border: 1px solid var(--border); cursor: pointer; background: var(--panel2); color: var(--text);
  transition: background .12s; }
.btn-modal:hover { background: var(--panel); }
.btn-modal.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-modal.primary:hover { background: var(--accent-h); }
.btn-modal.danger { background: var(--danger); color: #fff; border-color: var(--danger); }

.modal-warn { display: flex; gap: 9px; background: rgba(245,158,11,.1);
  border: 1px solid rgba(245,158,11,.3); border-radius: var(--r-sm);
  padding: 9px 11px; margin-bottom: 12px; font-size: 12.5px; line-height: 1.5; color: var(--body); }
.modal-warn i { color: var(--warn); font-size: 15px; flex-shrink: 0; margin-top: 1px; }

/* Upload drop zone */
.upload-zone { border: 2px dashed var(--border); border-radius: var(--r-md);
  padding: 18px; text-align: center; cursor: pointer; transition: all .15s;
  color: var(--muted); font-size: 12.5px; margin-bottom: 4px; }
.upload-zone:hover, .upload-zone.drag { border-color: var(--accent); color: var(--accent); background: var(--accent-dim); }
.upload-zone i { display: block; font-size: 1.6rem; margin-bottom: 6px; opacity: .6; }
#mdUploadInput { display: none; }

.spinner { display: inline-block; width: 12px; height: 12px;
  border: 2px solid rgba(255,255,255,.3); border-top-color: #fff;
  border-radius: 50%; animation: spin .6s linear infinite; margin-right: 5px; vertical-align: -2px; }
@keyframes spin { to { transform: rotate(360deg); } }

/* Sidebar toggle button — always visible */
#sidebarToggle { transition: transform .22s; }
#sidebarToggle.rotated { transform: rotate(180deg); }
</style>
</head>
<body>

<div id="app">
  <div id="backdrop" onclick="closeSidebar()"></div>

  <!-- Sidebar -->
  <aside id="sidebar">
    <div class="sb-head">
      <div class="sb-brand">
        <svg width="20" height="20" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
          <rect width="32" height="32" rx="7" fill="var(--accent)"/>
          <text x="5" y="23" font-family="Georgia,serif" font-size="22" font-weight="bold" fill="#fff">M</text>
          <rect x="22" y="12" width="6" height="2.5" rx="1.2" fill="rgba(255,255,255,.9)"/>
          <rect x="22" y="17" width="6" height="2.5" rx="1.2" fill="rgba(255,255,255,.6)"/>
          <rect x="22" y="22" width="4" height="2.5" rx="1.2" fill="rgba(255,255,255,.4)"/>
        </svg>
        Markdown Library
      </div>
      <input id="search" type="search" placeholder="Search files…" oninput="filterFiles()">
      <div class="sb-actions">
        <button class="btn-sb" onclick="loadFiles()" title="Refresh">
          <i class="bi bi-arrow-clockwise"></i>
        </button>
        <button class="btn-sb primary" id="topNewBtn" onclick="openNewModal('file')" title="New file or folder">
          <i class="bi bi-plus-lg"></i> New
        </button>
        <button class="btn-sb icon-only" onclick="openUploadMdModal()" title="Upload .md file">
          <i class="bi bi-upload"></i>
        </button>
        <button class="btn-sb d-md-none icon-only" onclick="closeSidebar()">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>
      <div id="ctxHint">
        <i class="bi bi-arrow-return-right"></i>
        <span id="ctxHintText"></span>
      </div>
    </div>
    <div id="fileList"></div>
  </aside>

  <!-- Main -->
  <div id="main">
    <div id="topbar">
      <!-- Desktop sidebar toggle -->
      <button class="icon-btn d-none d-md-flex" id="sidebarToggle" onclick="toggleSidebarDesktop()" title="Toggle sidebar">
        <i class="bi bi-layout-sidebar" style="font-size:15px"></i>
      </button>
      <!-- Mobile sidebar open -->
      <button class="icon-btn d-md-none" onclick="openSidebar()">
        <i class="bi bi-list" style="font-size:16px"></i>
      </button>

      <span id="title">
        <span style="color:var(--muted);font-size:12px">No file selected</span>
      </span>

      <div class="tab-group ms-auto">
        <button class="tab-btn active" id="tabPreview" onclick="setMode('preview')">
          <i class="bi bi-eye me-1"></i><span class="d-none d-sm-inline">Preview</span>
        </button>
        <button class="tab-btn" id="tabEditor" onclick="setMode('editor')">
          <i class="bi bi-pencil me-1"></i><span class="d-none d-sm-inline">Edit</span>
        </button>
      </div>

      <div class="dd-wrap">
        <button class="icon-btn" id="downloadBtn" onclick="toggleDdMenu(event)" title="Export">
          <i class="bi bi-download"></i>
        </button>
        <div class="dd-menu" id="ddMenu">
          <div class="dd-item" onclick="downloadAs('md')">
            <i class="bi bi-filetype-md"></i> Markdown <span class="ext">.md</span>
          </div>
          <div class="dd-item" onclick="downloadAs('txt')">
            <i class="bi bi-filetype-txt"></i> Plain text <span class="ext">.txt</span>
          </div>
          <div class="dd-item" onclick="downloadAs('html')">
            <i class="bi bi-filetype-html"></i> HTML <span class="ext">.html</span>
          </div>
          <div class="dd-item" onclick="downloadAs('pdf')">
            <i class="bi bi-filetype-pdf"></i> PDF <span class="ext">.pdf</span>
          </div>
        </div>
      </div>

      <button id="saveBtn" onclick="saveFile()">
        <i class="bi bi-floppy"></i><span class="d-none d-sm-inline">Save</span>
      </button>
      <span id="saveStatus"></span>

      <button class="icon-btn" id="installBtn" style="display:none" title="Install app" onclick="installApp()">
        <i class="bi bi-phone-vibrate"></i>
      </button>

      <button class="icon-btn" onclick="toggleTheme()" title="Toggle theme">
        <i id="themeIcon" class="bi bi-sun-fill"></i>
      </button>
    </div>

    <div id="workspace">
      <!-- Preview mode -->
      <div id="previewWrap">
        <div id="previewRendered">
          <article id="article" class="article">
            <div class="empty">
              <i class="bi bi-file-earmark-text"></i>
              <p>Select a file from the sidebar</p>
            </div>
          </article>
        </div>
      </div>

      <!-- Editor mode -->
      <div id="editorWrap" style="display:none">
        <div id="editorPane">
          <div class="pane-label">
            <span>Markdown <kbd style="font-size:9px;background:var(--panel2);border:1px solid var(--border);border-radius:3px;padding:1px 5px;color:var(--muted)">Ctrl+S</kbd></span>
            <span style="font-size:10px;color:var(--muted);opacity:.7">
              <i class="bi bi-cloud-upload" style="margin-right:3px"></i>Drop media
            </span>
          </div>
          <textarea id="editor" spellcheck="false"
            oninput="onEditorInput()" onkeydown="editorKeydown(event)"></textarea>
          <div id="dropOverlay">
            <i class="bi bi-cloud-upload-fill"></i>
            <span>Drop media here · max 1 GB</span>
          </div>
        </div>
        <div class="split-resizer" id="splitResizer" title="Drag to resize"></div>
        <div id="editorRenderedPane">
          <div class="pane-label">Preview</div>
          <article id="editorPreview" class="article"></article>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Upload toast -->
<div id="toast">
  <span id="toastMsg">Uploading…</span>
  <div class="toast-bar"><div class="toast-fill" id="toastFill"></div></div>
</div>

<!-- ── New File / Folder Modal ── -->
<div class="modal-bg" id="newModal" onclick="if(event.target===this)closeNewModal()">
  <div class="modal-box">
    <h5><i class="bi bi-plus-circle-fill"></i><span id="newModalTitle">Create new</span></h5>

    <!-- Type toggle -->
    <div class="modal-type-row">
      <button class="modal-type-btn active" id="typeFileBtn" onclick="setNewType('file')">
        <i class="bi bi-file-earmark-text"></i> File
      </button>
      <button class="modal-type-btn" id="typeFolderBtn" onclick="setNewType('folder')">
        <i class="bi bi-folder-plus"></i> Folder
      </button>
    </div>

    <!-- Context hint -->
    <div class="modal-ctx" id="newCtxRow">
      <i class="bi bi-folder2-open"></i>
      <span id="newCtxLabel">Creating in root</span>
    </div>

    <input class="modal-input" id="newFilename" placeholder="name"
      oninput="updateNewHint()"
      onkeydown="if(event.key==='Enter')confirmNew(); if(event.key==='Escape')closeNewModal()">
    <div class="modal-hint" id="newHint">Enter a name for the new file. <code>.md</code> added automatically.</div>
    <div class="modal-error" id="newError"></div>
    <div class="modal-footer">
      <button class="btn-modal" onclick="closeNewModal()">Cancel</button>
      <button class="btn-modal primary" id="newConfirmBtn" onclick="confirmNew()">Create</button>
    </div>
  </div>
</div>

<!-- ── Upload .md Modal ── -->
<div class="modal-bg" id="uploadMdModal" onclick="if(event.target===this)closeUploadMdModal()">
  <div class="modal-box">
    <h5><i class="bi bi-upload"></i> Upload Markdown file</h5>
    <div class="modal-ctx" id="uploadCtxRow">
      <i class="bi bi-folder2-open"></i>
      <span id="uploadCtxLabel">Uploading to root</span>
    </div>
    <div class="upload-zone" id="uploadZone" onclick="document.getElementById('mdUploadInput').click()"
      ondragover="uploadZoneDrag(event,true)" ondragleave="uploadZoneDrag(event,false)" ondrop="uploadZoneDrop(event)">
      <i class="bi bi-file-earmark-arrow-up"></i>
      <span id="uploadZoneLabel">Click to choose or drag a <strong>.md</strong> file</span>
    </div>
    <input type="file" id="mdUploadInput" accept=".md" onchange="uploadMdSelected(event)">
    <div class="modal-error" id="uploadMdError"></div>
    <div class="modal-footer">
      <button class="btn-modal" onclick="closeUploadMdModal()">Cancel</button>
    </div>
  </div>
</div>

<!-- ── Rename Modal ── -->
<div class="modal-bg" id="renameModal" onclick="if(event.target===this)closeRenameModal()">
  <div class="modal-box">
    <h5><i class="bi bi-pencil-square"></i> Rename</h5>
    <div class="modal-ctx" id="renameCtxRow">
      <i class="bi bi-file-earmark-text"></i>
      <span id="renameCtxLabel"></span>
    </div>
    <input class="modal-input" id="renameInput" placeholder="New name"
      onkeydown="if(event.key==='Enter')confirmRename(); if(event.key==='Escape')closeRenameModal()">
    <div class="modal-hint" id="renameHint"></div>
    <div class="modal-error" id="renameError"></div>
    <div class="modal-footer">
      <button class="btn-modal" onclick="closeRenameModal()">Cancel</button>
      <button class="btn-modal primary" onclick="confirmRename()">Rename</button>
    </div>
  </div>
</div>

<!-- ── Export warn Modal ── -->
<div class="modal-bg" id="exportWarnModal" onclick="if(event.target===this)closeExportWarnModal()">
  <div class="modal-box">
    <h5><i class="bi bi-exclamation-triangle" style="color:var(--warn)"></i>Media in this document</h5>
    <div class="modal-warn">
      <i class="bi bi-info-circle-fill"></i>
      <span id="exportWarnText"></span>
    </div>
    <div class="modal-footer">
      <button class="btn-modal" onclick="closeExportWarnModal()">Cancel</button>
      <button class="btn-modal primary" id="exportContinueBtn">Continue</button>
    </div>
  </div>
</div>

<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
window.mermaid = mermaid;
window.mermaidReady = true;
</script>

<script>
// ── State ──────────────────────────────────────────────────
let treeData    = { root_files: [], folders: [] };
let openFolders = new Set();
let current     = null;
let selectedFolder = null;
let mode        = 'preview';
let isDirty     = false;
let previewDebounce = null;
let theme       = 'dark';
let syncLock    = false;
let sidebarCollapsed = false;
// When set, this overrides the normal "context folder" resolution logic —
// used when the New modal is opened from a specific folder's own '+' button.
let modalContextOverride = null;

// ── Theme ──────────────────────────────────────────────────
function mermaidTheme(t) { return t === 'dark' ? 'dark' : 'default'; }

function applyTheme(t) {
  theme = t;
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('themeIcon').className = t === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
  document.getElementById('hlDark').disabled  = (t === 'light');
  document.getElementById('hlLight').disabled = (t === 'dark');
  document.querySelectorAll('pre code').forEach(b => {
    b.removeAttribute('data-highlighted');
    hljs.highlightElement(b);
  });
  if (window.mermaid) {
    window.mermaid.initialize({ startOnLoad: false, theme: mermaidTheme(t), securityLevel: 'loose' });
    document.querySelectorAll('.mermaid[data-src]').forEach(async el => {
      el.removeAttribute('data-processed');
      el.innerHTML = el.dataset.src;
      try { await window.mermaid.run({ nodes: [el] }); } catch(e) {}
    });
  }
  localStorage.setItem('md-theme', t);
}

function toggleTheme() {
  const next = theme === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  document.getElementById('themeColorMeta').content = next === 'light' ? '#f0f4f8' : '#0c1220';
}

// ── Sidebar ────────────────────────────────────────────────
function openSidebar()  {
  document.getElementById('sidebar').classList.add('show');
  document.getElementById('backdrop').classList.add('show');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('show');
  document.getElementById('backdrop').classList.remove('show');
}
function toggleSidebarDesktop() {
  sidebarCollapsed = !sidebarCollapsed;
  const sb  = document.getElementById('sidebar');
  const btn = document.getElementById('sidebarToggle');
  sb.classList.toggle('collapsed', sidebarCollapsed);
  btn.classList.toggle('rotated', sidebarCollapsed);
  localStorage.setItem('sb-collapsed', sidebarCollapsed ? '1' : '0');
}

// ── Context helpers ────────────────────────────────────────
function getContextFolder() {
  if (modalContextOverride !== null) return modalContextOverride;
  if (selectedFolder) return selectedFolder;
  if (current && current.includes('/')) {
    return current.split('/').slice(0, -1).join('/');
  }
  return null;
}

function updateCtxHint() {
  const folder = getContextFolder();
  const hint = document.getElementById('ctxHint');
  const text = document.getElementById('ctxHintText');
  if (folder) {
    text.textContent = `In: ${folder}`;
    hint.classList.add('show');
  } else {
    hint.classList.remove('show');
  }
}

// ── Folder animation ───────────────────────────────────────
// Animates a folder-children element open or closed by measuring
// its actual scrollHeight — no max-height guessing needed.
function animateFolderChildren(el, open) {
  // Cancel any in-progress transition cleanly
  el.style.transition = 'none';
  if (open) {
    el.style.display = 'block';
    el.style.overflow = 'hidden';
    const fullH = el.scrollHeight;
    el.style.height = '0px';
    // Double rAF ensures the browser has committed height=0 before we animate
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.style.transition = 'height .22s cubic-bezier(.4,0,.2,1)';
        el.style.height = fullH + 'px';
      });
    });
    el.addEventListener('transitionend', function done() {
      el.style.height = '';
      el.style.overflow = '';
      el.style.transition = '';
      el.removeEventListener('transitionend', done);
    }, { once: true });
  } else {
    el.style.height = el.scrollHeight + 'px';
    el.style.overflow = 'hidden';
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        el.style.transition = 'height .18s cubic-bezier(.4,0,.2,1)';
        el.style.height = '0px';
      });
    });
    el.addEventListener('transitionend', function done() {
      el.style.display = 'none';
      el.style.height = '';
      el.style.overflow = '';
      el.style.transition = '';
      el.removeEventListener('transitionend', done);
    }, { once: true });
  }
}

// ── File list ─────────────────────────────────────────────
async function loadFiles() {
  const res = await fetch('/api/files');
  treeData = await res.json();
  renderTree();
}

function makeRenameBtn(path, isFolder) {
  const btn = document.createElement('button');
  btn.className = 'row-btn';
  btn.title = 'Rename';
  btn.innerHTML = '<i class="bi bi-pencil"></i>';
  btn.onclick = e => { e.stopPropagation(); openRenameModal(path, isFolder); };
  return btn;
}

function makeFolderAddBtn(path) {
  const btn = document.createElement('button');
  btn.className = 'row-btn';
  btn.title = 'New file or folder here';
  btn.innerHTML = '<i class="bi bi-plus-lg"></i>';
  btn.onclick = e => { e.stopPropagation(); openNewModalForFolder(path); };
  return btn;
}

function makeFileItem(file, isRoot) {
  const div = document.createElement('div');
  const isReadme = file.toLowerCase() === 'readme.md';
  const name = file.split('/').pop().replace(/\.md$/i, '');
  div.className = 'file-item' + (isRoot ? ' root' : '') + (file === current ? ' active' : '') + (isReadme ? ' readme' : '');
  div.title = file;

  const nameSpan = document.createElement('span');
  nameSpan.className = 'fi-name';
  if (isReadme) {
    nameSpan.innerHTML = `<i class="bi bi-bookmark-star-fill" style="font-size:10px;margin-right:4px"></i>${name}`;
  } else {
    nameSpan.textContent = name;
  }
  div.appendChild(nameSpan);

  const actions = document.createElement('span');
  actions.className = 'row-actions';
  actions.appendChild(makeRenameBtn(file, false));
  div.appendChild(actions);

  div.onclick = () => {
    selectedFolder = null;
    updateCtxHint();
    openFile(file, true);
    closeSidebar();
  };
  return div;
}

function renderTree() {
  const list = document.getElementById('fileList');
  list.innerHTML = '';
  const rootSorted = [...treeData.root_files].sort((a, b) => {
    if (a.toLowerCase() === 'readme.md') return -1;
    if (b.toLowerCase() === 'readme.md') return 1;
    return a.localeCompare(b);
  });
  rootSorted.forEach(f => list.appendChild(makeFileItem(f, true)));

  treeData.folders.forEach(folder => {
    const isOpen = openFolders.has(folder.name);
    const isSelected = selectedFolder === folder.name;

    const row = document.createElement('div');
    row.className = 'folder-row' + (isOpen ? ' open' : '') + (isSelected ? ' selected' : '');

    const chevron = document.createElement('i');
    chevron.className = 'bi bi-chevron-right folder-chevron';
    // Set initial rotation state via inline style to match isOpen
    if (isOpen) chevron.style.transform = 'rotate(90deg)';

    const icon = document.createElement('i');
    icon.className = `bi bi-folder${isOpen ? '2-open' : ''}-fill`;
    icon.style.cssText = 'font-size:11px;opacity:.55';

    const nameSpan = document.createElement('span');
    nameSpan.style.cssText = 'flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap';
    nameSpan.textContent = folder.name;

    const count = document.createElement('span');
    count.className = 'folder-count';
    count.textContent = folder.files.length;

    const actions = document.createElement('span');
    actions.className = 'row-actions';
    actions.appendChild(makeFolderAddBtn(folder.name));
    actions.appendChild(makeRenameBtn(folder.name, true));

    row.appendChild(chevron);
    row.appendChild(icon);
    row.appendChild(nameSpan);
    row.appendChild(count);
    row.appendChild(actions);

    // Build children container — hidden initially if folder is closed
    const children = document.createElement('div');
    children.className = 'folder-children';
    if (folder.files.length) {
      folder.files.forEach(f => children.appendChild(makeFileItem(f, false)));
    } else {
      const emptyHint = document.createElement('div');
      emptyHint.className = 'folder-empty-hint';
      emptyHint.textContent = 'Empty folder';
      children.appendChild(emptyHint);
    }
    if (!isOpen) children.style.display = 'none';

    row.onclick = (e) => {
      if (e.target.closest('.row-actions')) return;
      const opening = !openFolders.has(folder.name);
      if (opening) {
        openFolders.add(folder.name);
        selectedFolder = folder.name;
      } else {
        openFolders.delete(folder.name);
        if (selectedFolder === folder.name) selectedFolder = null;
      }

      // Animate children in place — no full re-render needed
      animateFolderChildren(children, opening);

      // Update row classes and icon/chevron directly
      row.classList.toggle('open', opening);
      row.classList.toggle('selected', opening || selectedFolder === folder.name);
      chevron.style.transform = opening ? 'rotate(90deg)' : '';
      icon.className = `bi bi-folder${opening ? '2-open' : ''}-fill`;
      icon.style.cssText = 'font-size:11px;opacity:.55';

      updateCtxHint();
    };

    list.appendChild(row);
    list.appendChild(children);
  });

  // Only show the top-level "New" button when there are no folders in
  // the root yet — once folders exist, use each folder's own '+' button.
  document.getElementById('topNewBtn').style.display = treeData.folders.length ? 'none' : 'flex';
}

function toggleFolder(name) {
  if (openFolders.has(name)) {
    openFolders.delete(name);
  } else {
    openFolders.add(name);
  }
  renderTree();
}

function filterFiles() {
  const q = document.getElementById('search').value.toLowerCase().trim();
  if (!q) { renderTree(); return; }
  const all = [...treeData.root_files, ...treeData.folders.flatMap(f => f.files)];
  const matched = all.filter(f => f.toLowerCase().includes(q));
  const list = document.getElementById('fileList');
  list.innerHTML = '';
  matched.forEach(file => {
    const div = document.createElement('div');
    const isReadme = file.toLowerCase() === 'readme.md';
    const parts = file.split('/');
    const name = parts.pop().replace(/\.md$/i, '');
    const folder = parts.join('/');
    div.className = 'file-item root' + (file === current ? ' active' : '') + (isReadme ? ' readme' : '');
    div.title = file;
    const nameSpan = document.createElement('span');
    nameSpan.className = 'fi-name';
    if (folder) {
      nameSpan.innerHTML = `<span class="folder-tag">${folder}/</span>${name}`;
    } else if (isReadme) {
      nameSpan.innerHTML = `<i class="bi bi-bookmark-star-fill" style="font-size:10px;margin-right:4px"></i>${name}`;
    } else {
      nameSpan.textContent = name;
    }
    div.appendChild(nameSpan);
    div.onclick = () => { openFile(file, true); closeSidebar(); };
    list.appendChild(div);
  });
  if (!matched.length) {
    list.innerHTML = `<div style="padding:20px 12px;font-size:12px;color:var(--muted);text-align:center">No results</div>`;
  }
}

// ── Open file ─────────────────────────────────────────────
async function openFile(file, updateUrl) {
  if (!file) return;
  if (isDirty && current && !confirm(`Discard unsaved changes in "${current}"?`)) return;
  current = file;
  isDirty = false;
  setStatus('');
  if (updateUrl) setUrlFile(file);
  const parts = file.split('/');
  if (parts.length > 1) openFolders.add(parts[0]);
  renderTree();

  const pretty = slugTitle(file);
  document.getElementById('title').innerHTML =
    `<span class="name">${pretty}</span>
     <span style="color:var(--muted);font-size:11px;margin-left:6px">${file}</span>`;
  document.getElementById('saveBtn').style.display = 'none';
  document.title = `${pretty} — Markdown Library`;

  const res = await fetch('/api/read?file=' + encodeURIComponent(file));
  if (!res.ok) { alert('Could not load file.'); return; }
  const md = await res.text();
  document.getElementById('editor').value = md;
  await Promise.all([renderInto('article', md), renderInto('editorPreview', md)]);
  setupSyncScroll();
}

// ── Render ─────────────────────────────────────────────────
async function renderInto(id, md) {
  const res = await fetch('/api/render', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ markdown: md }),
  });
  const el = document.getElementById(id);
  el.innerHTML = await res.text();

  const footer = document.createElement('div');
  footer.className = 'article-footer';
  footer.innerHTML = `<span style="font-family:monospace;font-size:10px">${current || ''}</span>
    <span>${new Date().toLocaleDateString(undefined, { year:'numeric', month:'short', day:'numeric' })}</span>`;
  el.appendChild(footer);

  el.querySelectorAll('pre code').forEach(b => {
    b.removeAttribute('data-highlighted');
    hljs.highlightElement(b);
  });
  if (window.MathJax) await window.MathJax.typesetPromise([el]);
  if (window.mermaid) {
    const nodes = [...el.querySelectorAll('.mermaid')];
    if (nodes.length) {
      nodes.forEach(n => { n.dataset.src = n.innerHTML; });
      window.mermaid.initialize({ startOnLoad: false, theme: mermaidTheme(theme), securityLevel: 'loose' });
      try { await window.mermaid.run({ nodes }); } catch(e) {}
    }
  }
}

// ── Sync scroll ───────────────────────────────────────────
function setupSyncScroll() {
  const src  = document.getElementById('editor');
  const rend = document.getElementById('editorRenderedPane');
  src._syncHandler  && src.removeEventListener('scroll', src._syncHandler);
  rend._syncHandler && rend.removeEventListener('scroll', rend._syncHandler);
  src._syncHandler = () => {
    if (syncLock) return;
    syncLock = true;
    const r = src.scrollTop / Math.max(1, src.scrollHeight - src.clientHeight);
    rend.scrollTop = r * (rend.scrollHeight - rend.clientHeight);
    requestAnimationFrame(() => { syncLock = false; });
  };
  rend._syncHandler = () => {
    if (syncLock) return;
    syncLock = true;
    const r = rend.scrollTop / Math.max(1, rend.scrollHeight - rend.clientHeight);
    src.scrollTop = r * (src.scrollHeight - src.clientHeight);
    requestAnimationFrame(() => { syncLock = false; });
  };
  src.addEventListener('scroll',  src._syncHandler,  { passive: true });
  rend.addEventListener('scroll', rend._syncHandler, { passive: true });
}

// ── Mode ──────────────────────────────────────────────────
function setMode(m) {
  mode = m;
  document.getElementById('previewWrap').style.display = m === 'preview' ? 'flex' : 'none';
  document.getElementById('editorWrap' ).style.display = m === 'editor'  ? 'flex' : 'none';
  document.getElementById('saveBtn'    ).style.display = m === 'editor'  ? 'flex' : 'none';
  document.getElementById('tabPreview').classList.toggle('active', m === 'preview');
  document.getElementById('tabEditor' ).classList.toggle('active', m === 'editor');
}

// ── Editor ────────────────────────────────────────────────
function onEditorInput() {
  if (!isDirty) {
    isDirty = true;
    setStatus('unsaved');
    document.getElementById('saveBtn').style.display = 'flex';
  }
  clearTimeout(previewDebounce);
  previewDebounce = setTimeout(() => renderInto('editorPreview', document.getElementById('editor').value), 400);
}

function editorKeydown(e) {
  if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveFile(); }
  if (e.key === 'Tab') {
    e.preventDefault();
    const ta = e.target, s = ta.selectionStart, end = ta.selectionEnd;
    ta.value = ta.value.substring(0, s) + '    ' + ta.value.substring(end);
    ta.selectionStart = ta.selectionEnd = s + 4;
  }
}

// ── Save ──────────────────────────────────────────────────
async function saveFile() {
  if (!current) return;
  const res = await fetch('/api/save', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: current, content: document.getElementById('editor').value }),
  });
  if (res.ok) {
    isDirty = false;
    setStatus('saved');
    setTimeout(() => setStatus(''), 2500);
  } else {
    setStatus('error');
  }
}

function setStatus(s) {
  const el = document.getElementById('saveStatus');
  el.className = s || '';
  el.textContent = s === 'saved' ? '✓ Saved' : s === 'unsaved' ? '● Unsaved' : s === 'error' ? '✕ Failed' : '';
}

// ── New file/folder Modal ─────────────────────────────────
let newType = 'file';

function setNewType(t) {
  newType = t;
  document.getElementById('typeFileBtn').classList.toggle('active', t === 'file');
  document.getElementById('typeFolderBtn').classList.toggle('active', t === 'folder');
  updateNewHint();
  const input = document.getElementById('newFilename');
  input.placeholder = t === 'file' ? 'filename' : 'folder-name';
  input.focus();
}

function updateNewHint() {
  const val = document.getElementById('newFilename').value.trim();
  const hint = document.getElementById('newHint');
  const ctx = getContextFolder();
  if (newType === 'file') {
    const full = ctx ? `${ctx}/${val || 'name'}.md` : `${val || 'name'}.md`;
    hint.innerHTML = `Creates: <code>${full}</code>`;
  } else {
    const full = ctx ? `${ctx}/${val || 'name'}` : `${val || 'name'}`;
    hint.innerHTML = `Creates folder: <code>${full}</code>`;
  }
}

function openNewModal(type) {
  newType = type || 'file';
  document.getElementById('typeFileBtn').classList.toggle('active', newType === 'file');
  document.getElementById('typeFolderBtn').classList.toggle('active', newType === 'folder');
  document.getElementById('newFilename').value = '';
  document.getElementById('newError').style.display = 'none';
  const ctx = getContextFolder();
  document.getElementById('newCtxLabel').textContent = ctx ? `Creating inside: ${ctx}` : 'Creating in root';
  updateNewHint();
  document.getElementById('newModal').classList.add('show');
  setTimeout(() => document.getElementById('newFilename').focus(), 50);
}

// Opens the New modal locked to a specific folder's context, regardless
// of the currently selected folder or open file — used by each folder
// row's own '+' button.
function openNewModalForFolder(folderName) {
  modalContextOverride = folderName;
  openNewModal('file');
}

function closeNewModal() {
  document.getElementById('newModal').classList.remove('show');
  modalContextOverride = null;
}

async function confirmNew() {
  const name = document.getElementById('newFilename').value.trim();
  const err  = document.getElementById('newError');
  err.style.display = 'none';
  if (!name) { err.textContent = 'Enter a name.'; err.style.display = 'block'; return; }

  const ctx = getContextFolder();

  if (newType === 'folder') {
    const folderPath = ctx ? `${ctx}/${name}` : name;
    const res = await fetch('/api/mkdir', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder: folderPath }),
    });
    const data = await res.json();
    if (!data.ok) { err.textContent = data.error; err.style.display = 'block'; return; }
    closeNewModal();
    selectedFolder = folderPath.split('/')[0];
    openFolders.add(selectedFolder);
    await loadFiles();
    updateCtxHint();
  } else {
    let filePath = name.endsWith('.md') ? name : name + '.md';
    if (ctx) filePath = `${ctx}/${filePath}`;
    const res = await fetch('/api/new', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file: filePath }),
    });
    const data = await res.json();
    if (!data.ok) { err.textContent = data.error; err.style.display = 'block'; return; }
    closeNewModal();
    await loadFiles();
    setMode('editor');
    await openFile(data.file, true);
  }
}

// ── Upload .md Modal ──────────────────────────────────────
function openUploadMdModal() {
  document.getElementById('uploadMdError').style.display = 'none';
  document.getElementById('uploadZoneLabel').innerHTML = 'Click to choose or drag a <strong>.md</strong> file';
  document.getElementById('mdUploadInput').value = '';
  const ctx = getContextFolder();
  document.getElementById('uploadCtxLabel').textContent = ctx ? `Uploading to: ${ctx}` : 'Uploading to root';
  document.getElementById('uploadMdModal').classList.add('show');
}
function closeUploadMdModal() { document.getElementById('uploadMdModal').classList.remove('show'); }

function uploadZoneDrag(e, enter) {
  e.preventDefault();
  document.getElementById('uploadZone').classList.toggle('drag', enter);
}

function uploadZoneDrop(e) {
  e.preventDefault();
  document.getElementById('uploadZone').classList.remove('drag');
  const file = e.dataTransfer.files[0];
  if (file) doUploadMd(file);
}

function uploadMdSelected(e) {
  const file = e.target.files[0];
  if (file) doUploadMd(file);
}

async function doUploadMd(file) {
  const err = document.getElementById('uploadMdError');
  err.style.display = 'none';
  if (!file.name.toLowerCase().endsWith('.md')) {
    err.textContent = 'Only .md files are allowed.';
    err.style.display = 'block';
    return;
  }
  document.getElementById('uploadZoneLabel').innerHTML = `<span class="spinner"></span> Uploading ${file.name}…`;
  const ctx = getContextFolder();
  const fd = new FormData();
  fd.append('file', file);
  if (ctx) fd.append('folder', ctx);
  const res = await fetch('/api/upload-md', { method: 'POST', body: fd });
  const data = await res.json();
  if (!data.ok) {
    err.textContent = data.error;
    err.style.display = 'block';
    document.getElementById('uploadZoneLabel').innerHTML = 'Click to choose or drag a <strong>.md</strong> file';
    return;
  }
  closeUploadMdModal();
  if (ctx) openFolders.add(ctx.split('/')[0]);
  await loadFiles();
  await openFile(data.file, true);
}

// ── Rename Modal ──────────────────────────────────────────
let renameTarget = null;

function openRenameModal(path, isFolder) {
  renameTarget = { path, isFolder };
  document.getElementById('renameError').style.display = 'none';
  const parts = path.split('/');
  const name = parts[parts.length - 1];
  const ctx = parts.length > 1 ? parts.slice(0, -1).join('/') : null;
  const ctxRow = document.getElementById('renameCtxRow');
  ctxRow.querySelector('i').className = isFolder ? 'bi bi-folder2-open' : 'bi bi-file-earmark-text';
  document.getElementById('renameCtxLabel').textContent = isFolder
    ? `Folder: ${path}`
    : `File: ${path}`;

  const input = document.getElementById('renameInput');
  input.value = isFolder ? name : name.replace(/\.md$/i, '');
  document.getElementById('renameHint').innerHTML = isFolder
    ? (ctx ? `Inside: <code>${ctx}</code>` : 'In root')
    : (ctx ? `Inside: <code>${ctx}</code> · <code>.md</code> added automatically` : 'In root · <code>.md</code> added automatically');

  document.getElementById('renameModal').classList.add('show');
  setTimeout(() => { input.select(); input.focus(); }, 50);
}
function closeRenameModal() { document.getElementById('renameModal').classList.remove('show'); }

async function confirmRename() {
  const err = document.getElementById('renameError');
  err.style.display = 'none';
  if (!renameTarget) return;
  const newName = document.getElementById('renameInput').value.trim();
  if (!newName) { err.textContent = 'Enter a name.'; err.style.display = 'block'; return; }

  const { path, isFolder } = renameTarget;
  const parts = path.split('/');
  const parent = parts.length > 1 ? parts.slice(0, -1).join('/') : null;

  let newPath;
  if (isFolder) {
    newPath = parent ? `${parent}/${newName}` : newName;
  } else {
    const newFile = newName.endsWith('.md') ? newName : newName + '.md';
    newPath = parent ? `${parent}/${newFile}` : newFile;
  }

  const res = await fetch('/api/rename', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old: path, new: newPath }),
  });
  const data = await res.json();
  if (!data.ok) { err.textContent = data.error; err.style.display = 'block'; return; }

  closeRenameModal();
  if (isFolder) {
    if (selectedFolder === path) selectedFolder = newPath.split('/')[0];
    if (openFolders.has(path)) { openFolders.delete(path); openFolders.add(newPath); }
    if (current && current.startsWith(path + '/')) {
      current = newPath + current.slice(path.length);
    }
  } else {
    if (current === path) {
      current = newPath;
      setUrlFile(newPath);
    }
  }
  await loadFiles();
  if (current) {
    const pretty = slugTitle(current);
    document.getElementById('title').innerHTML =
      `<span class="name">${pretty}</span>
       <span style="color:var(--muted);font-size:11px;margin-left:6px">${current}</span>`;
    document.title = `${pretty} — Markdown Library`;
  }
}

// ── Download / Export ─────────────────────────────────────
function toggleDdMenu(e) {
  e.stopPropagation();
  document.getElementById('ddMenu').classList.toggle('show');
}
document.addEventListener('click', () => document.getElementById('ddMenu').classList.remove('show'));

function baseName() {
  return current ? current.split('/').pop().replace(/\.md$/i, '') : 'document';
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function hasAVMedia() {
  return /<video[\s\S]*?>|<audio[\s\S]*?>/i.test(document.getElementById('editor').value);
}

function confirmMediaStrip(fmt) {
  return new Promise(resolve => {
    if (!hasAVMedia()) { resolve(true); return; }
    const modal = document.getElementById('exportWarnModal');
    const btn   = document.getElementById('exportContinueBtn');
    document.getElementById('exportWarnText').textContent = fmt === 'pdf'
      ? 'This document contains video/audio. Each will be replaced with an icon and link in the PDF.'
      : 'This document contains video/audio. They will be playable in the HTML only if the server is reachable.';
    const done = ok => { modal.classList.remove('show'); btn.onclick = null; resolve(ok); };
    btn.onclick = () => done(true);
    modal._cancel = () => done(false);
    modal.classList.add('show');
  });
}
function closeExportWarnModal() {
  const m = document.getElementById('exportWarnModal');
  m.classList.remove('show');
  if (m._cancel) m._cancel();
}

async function downloadAs(fmt) {
  document.getElementById('ddMenu').classList.remove('show');
  if (!current) { alert('Open a file first.'); return; }
  const name = baseName();
  if (fmt === 'md') {
    triggerDownload(new Blob([document.getElementById('editor').value], { type: 'text/markdown' }), `${name}.md`);
    return;
  }
  if (fmt === 'txt') {
    const res = await fetch('/api/export/txt', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ markdown: document.getElementById('editor').value, filename: name }),
    });
    if (!res.ok) { alert('Text export failed.'); return; }
    triggerDownload(await res.blob(), `${name}.txt`);
    return;
  }
  if (fmt === 'html') {
    if (!await confirmMediaStrip('html')) return;
    await exportHtml(name); return;
  }
  if (fmt === 'pdf') {
    if (!await confirmMediaStrip('pdf')) return;
    await exportPdf(name); return;
  }
}

async function exportHtml(name) {
  setBusy(true);
  try {
    const src = document.getElementById(mode === 'editor' ? 'editorPreview' : 'article');
    const clone = src.cloneNode(true);
    await inlineImages(clone);
    await inlineAV(clone);
    let css = '';
    for (const sheet of document.styleSheets) {
      try {
        for (const rule of sheet.cssRules)
          if (rule.selectorText?.includes('.article')) css += rule.cssText + '\n';
      } catch(e) {}
    }
    triggerDownload(new Blob([`<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>${esc(name)}</title><style>body{margin:0;padding:40px 16px;background:#0c1220;font-family:system-ui,sans-serif}
${css}</style></head><body>${clone.outerHTML}</body></html>`], { type: 'text/html' }), `${name}.html`);
  } catch(e) { alert('HTML export failed: ' + e.message); }
  finally { setBusy(false); }
}

async function exportPdf(name) {
  setBusy(true);
  try {
    const src = document.getElementById(mode === 'editor' ? 'editorPreview' : 'article');
    const clone = src.cloneNode(true);
    await inlineImages(clone);
    await rasterizeMath(clone);
    replaceAV(clone);
    const css = `body{font-family:Helvetica,Arial,sans-serif;color:#1a1a1a;font-size:11pt;line-height:1.6}
h1{font-size:20pt}h2{font-size:15pt;border-bottom:1px solid #ccc;padding-bottom:4pt}
h3{font-size:12.5pt}p,li{margin-bottom:5pt}
code{font-family:Courier,monospace;font-size:9.5pt;background:#f1f1f1;padding:1pt 3pt}
pre{font-family:Courier,monospace;font-size:9pt;background:#f5f5f5;padding:8pt;border:1px solid #ddd}
pre code{background:transparent;padding:0}
blockquote{border-left:2px solid #888;padding-left:10pt;color:#444;margin:0}
table{width:100%;border-collapse:collapse}th,td{border:1px solid #999;padding:5pt 8pt}
th{background:#eee}img{max-width:100%}
.media-placeholder{border:1px solid #ccc;padding:8pt;margin:8pt 0;background:#fafafa}
.article-footer{font-size:8pt;color:#888;border-top:1px solid #ccc;margin-top:20pt;padding-top:5pt}`;
    const res = await fetch('/api/export/pdf', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ html: `<!DOCTYPE html><html><head><meta charset="UTF-8"><style>${css}</style></head><body>${clone.innerHTML}</body></html>`, filename: name }),
    });
    if (!res.ok) {
      let msg = 'PDF export failed.';
      try { const d = await res.json(); if (d.error) msg = d.error; } catch(_) {}
      alert(msg); return;
    }
    triggerDownload(await res.blob(), `${name}.pdf`);
  } catch(e) { alert('PDF export failed: ' + e.message); }
  finally { setBusy(false); }
}

function setBusy(busy) {
  const btn = document.getElementById('downloadBtn');
  if (busy) { btn._prev = btn.innerHTML; btn.innerHTML = '<span class="spinner"></span>'; btn.style.pointerEvents = 'none'; }
  else { if (btn._prev) btn.innerHTML = btn._prev; btn.style.pointerEvents = ''; }
}

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

async function inlineImages(el) {
  await Promise.all([...el.querySelectorAll('img')].map(async img => {
    const src = img.getAttribute('src');
    if (!src || src.startsWith('data:')) return;
    try {
      const r = await fetch(new URL(src, location.href).href);
      img.setAttribute('src', await blobToDataUrl(await r.blob()));
    } catch(e) {}
  }));
}

async function inlineAV(el) {
  await Promise.all([...el.querySelectorAll('video, audio')].map(async m => {
    const src = m.getAttribute('src');
    if (!src || src.startsWith('data:')) return;
    try {
      const r = await fetch(new URL(src, location.href).href);
      m.setAttribute('src', await blobToDataUrl(await r.blob()));
    } catch(e) {}
  }));
}

function replaceAV(el) {
  el.querySelectorAll('video, audio').forEach(m => {
    const isV = m.tagName.toLowerCase() === 'video';
    const src = m.getAttribute('src') || '';
    const title = m.getAttribute('title') || src.split('/').pop() || 'media';
    const div = document.createElement('div');
    div.className = 'media-placeholder';
    div.innerHTML = `${isV ? '🎬' : '🔊'} <strong>${esc(title)}</strong> — ${isV ? 'video' : 'audio'} (not embeddable in PDF)<br><a href="${src}">${src}</a>`;
    m.replaceWith(div);
  });
}

async function rasterizeMath(el) {
  await Promise.all([...el.querySelectorAll('.mermaid svg, mjx-container svg')].map(async svg => {
    try {
      const img = document.createElement('img');
      img.src = await svgToPng(svg);
      img.style.maxWidth = '100%';
      (svg.closest('.mermaid') || svg.closest('mjx-container') || svg).replaceWith(img);
    } catch(e) {}
  }));
}

function svgToPng(svgEl) {
  return new Promise((res, rej) => {
    const clone = svgEl.cloneNode(true);
    const bbox = svgEl.getBoundingClientRect();
    const w = Math.max(bbox.width, 50) * 2, h = Math.max(bbox.height, 20) * 2;
    clone.setAttribute('width', w); clone.setAttribute('height', h);
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml;charset=utf-8' }));
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h); ctx.drawImage(img, 0, 0, w, h);
      URL.revokeObjectURL(url); res(c.toDataURL('image/png'));
    };
    img.onerror = e => { URL.revokeObjectURL(url); rej(e); };
    img.src = url;
  });
}

function blobToDataUrl(blob) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(blob);
  });
}

// ── Media upload (editor drop) ────────────────────────────
function buildMediaMarkdown(url, name, type) {
  const ext = name.split('.').pop().toLowerCase();
  if (['jpg','jpeg','png','gif','webp','svg','bmp','avif'].includes(ext)) return `![${name}](${url})`;
  if (['mp4','webm','ogg','mov','mkv','avi'].includes(ext)) return `<video controls src="${url}" title="${name}"></video>`;
  if (['mp3','wav','aac','flac','m4a','opus'].includes(ext)) return `<audio controls src="${url}" title="${name}"></audio>`;
  return `[${name}](${url})`;
}

function insertAtCursor(text) {
  const ta = document.getElementById('editor');
  const s = ta.selectionStart, e = ta.selectionEnd;
  const before = ta.value.substring(0, s), after = ta.value.substring(e);
  const pre  = (before.length && !before.endsWith('\n')) ? '\n' : '';
  const post = (after.length  && !after.startsWith('\n')) ? '\n' : '';
  ta.value = before + pre + text + post + after;
  ta.selectionStart = ta.selectionEnd = s + pre.length + text.length + post.length;
  ta.focus(); onEditorInput();
}

function showToast(msg, pct, ok) {
  const t = document.getElementById('toast');
  document.getElementById('toastMsg').textContent = msg;
  const fill = document.getElementById('toastFill');
  fill.style.width = pct + '%';
  fill.style.background = ok === false ? 'var(--danger)' : ok === true ? 'var(--success)' : 'var(--accent)';
  t.classList.add('show');
}
function hideToast() { setTimeout(() => document.getElementById('toast').classList.remove('show'), 1600); }

async function uploadFile(file) {
  if (file.size > 1000 * 1024 * 1024) {
    showToast('File exceeds 1 GB', 100, false); hideToast(); return;
  }
  showToast(`Uploading ${file.name}…`, 30, null);
  const fd = new FormData(); fd.append('file', file);
  const res = await fetch('/api/upload', { method: 'POST', body: fd });
  const data = await res.json();
  if (!data.ok) { showToast('Upload failed: ' + data.error, 100, false); hideToast(); return; }
  showToast(`Inserted ${file.name}`, 100, true); hideToast();
  insertAtCursor(buildMediaMarkdown(`/artifacts/${data.filename}`, data.original, file.type));
}

(function setupDrop() {
  const pane = document.getElementById('editorPane');
  const overlay = document.getElementById('dropOverlay');
  let dragCount = 0;
  pane.addEventListener('dragenter', e => {
    e.preventDefault();
    if ([...e.dataTransfer.items].some(i => i.kind === 'file')) { dragCount++; overlay.classList.add('active'); }
  });
  pane.addEventListener('dragleave', () => { if (--dragCount <= 0) { dragCount = 0; overlay.classList.remove('active'); } });
  pane.addEventListener('dragover', e => e.preventDefault());
  pane.addEventListener('drop', async e => {
    e.preventDefault(); dragCount = 0; overlay.classList.remove('active');
    if (!current) { alert('Open a file first to insert media.'); return; }
    if (mode !== 'editor') setMode('editor');
    for (const f of [...e.dataTransfer.files]) await uploadFile(f);
  });
})();

document.getElementById('editor').addEventListener('paste', async e => {
  const text = e.clipboardData.getData('text/plain').trim();
  if (!text) return;
  try {
    const url = new URL(text);
    if (!['http:','https:'].includes(url.protocol)) return;
    const ext = url.pathname.split('.').pop().toLowerCase().split('?')[0];
    let snippet = null;
    if (['jpg','jpeg','png','gif','webp','svg'].includes(ext)) snippet = `![image](${text})`;
    else if (['mp4','webm','ogg','mov'].includes(ext)) snippet = `<video controls src="${text}"></video>`;
    else if (['mp3','wav','aac','flac'].includes(ext)) snippet = `<audio controls src="${text}"></audio>`;
    if (snippet) { e.preventDefault(); insertAtCursor(snippet); }
  } catch(_) {}
});

// ── Draggable split resizer (editor <-> preview) ──────────
(function setupSplitResizer() {
  const resizer = document.getElementById('splitResizer');
  const wrap    = document.getElementById('editorWrap');
  const pane    = document.getElementById('editorPane');
  let dragging  = false;

  function isColumn() { return window.matchMedia('(max-width: 767px)').matches; }

  function setSplit(pct) {
    pct = Math.min(80, Math.max(20, pct));
    pane.style.flex = `0 0 ${pct}%`;
    localStorage.setItem('split-pct', String(pct));
  }

  function clientCoords(e) {
    if (e.touches && e.touches.length) return { x: e.touches[0].clientX, y: e.touches[0].clientY };
    return { x: e.clientX, y: e.clientY };
  }

  function onMove(e) {
    if (!dragging) return;
    const rect = wrap.getBoundingClientRect();
    const { x, y } = clientCoords(e);
    let pct;
    if (isColumn()) {
      pct = ((y - rect.top) / rect.height) * 100;
    } else {
      pct = ((x - rect.left) / rect.width) * 100;
    }
    setSplit(pct);
  }

  function startDrag(e) {
    dragging = true;
    resizer.classList.add('dragging');
    document.body.style.cursor = isColumn() ? 'row-resize' : 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  }

  function stopDrag() {
    if (!dragging) return;
    dragging = false;
    resizer.classList.remove('dragging');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  }

  resizer.addEventListener('mousedown', startDrag);
  resizer.addEventListener('touchstart', startDrag, { passive: false });
  window.addEventListener('mousemove', onMove);
  window.addEventListener('touchmove', onMove, { passive: true });
  window.addEventListener('mouseup', stopDrag);
  window.addEventListener('touchend', stopDrag);

  const saved = parseFloat(localStorage.getItem('split-pct'));
  setSplit(!isNaN(saved) ? saved : 50);
})();

// ── URL helpers ───────────────────────────────────────────
function getUrlFile() { return new URLSearchParams(location.search).get('file') || ''; }
function setUrlFile(f) {
  const url = new URL(location.href);
  f ? url.searchParams.set('file', f) : url.searchParams.delete('file');
  history.pushState({ file: f }, '', url.toString());
}
function slugTitle(f) {
  if (!f) return '';
  return f.split('/').pop().replace(/\.md$/i, '').replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

window.addEventListener('popstate', e => {
  const f = e.state?.file || getUrlFile();
  if (f && f !== current) openFile(f, false);
});

// ── PWA install ───────────────────────────────────────────
let _installPrompt = null;
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  _installPrompt = e;
  document.getElementById('installBtn').style.display = 'flex';
});
async function installApp() {
  if (!_installPrompt) return;
  _installPrompt.prompt();
  const { outcome } = await _installPrompt.userChoice;
  if (outcome === 'accepted') document.getElementById('installBtn').style.display = 'none';
  _installPrompt = null;
}
window.addEventListener('appinstalled', () => {
  document.getElementById('installBtn').style.display = 'none';
});

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(e => console.warn('SW:', e));
}

// ── Init ──────────────────────────────────────────────────
(async () => {
  applyTheme(localStorage.getItem('md-theme') || 'dark');
  if (localStorage.getItem('sb-collapsed') === '1') {
    sidebarCollapsed = true;
    document.getElementById('sidebar').classList.add('collapsed');
    document.getElementById('sidebarToggle').classList.add('rotated');
  }
  await loadFiles();
  const f = getUrlFile() || __INITIAL_FILE__;
  if (f) await openFile(f, !getUrlFile());
})();
</script>
</body>
</html>
"""


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Markdown Library")
    print("=" * 60)
    print(f"  Markdown : {base_path}")
    print(f"  Artifacts: {artifact_path}")
    print(f"  URL      : http://{HOST}:{PORT}")
    print("=" * 60)
    app.run(host=HOST, port=PORT, debug=False)
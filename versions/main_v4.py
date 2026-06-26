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

# ============================================================
# CONFIG — edit these
# ============================================================

MARKDOWN_FOLDER  = r"/home/mohittewari/papers/papers/summary"
ARTIFACT_DIR     = r"/home/mohittewari/papers/artifacts"
HOST             = "0.0.0.0"
PORT             = 8890
MAX_UPLOAD_BYTES = 1000 * 1024 * 1024  # 1 GB

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


def resolve_safe_path(relative_path):
    relative_path = relative_path.split("?")[0].split("#")[0]
    if not relative_path.lower().endswith(".md"):
        return None
    safe = (base_path / relative_path).resolve()
    if not str(safe).startswith(str(base_path)):
        return None
    return safe


def read_markdown(relative_path):
    safe = resolve_safe_path(relative_path)
    if safe is None or not safe.exists():
        return None
    return safe.read_text(encoding="utf-8")


def write_markdown(relative_path, content):
    safe = resolve_safe_path(relative_path)
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
    safe = resolve_safe_path(file)
    if safe is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 403
    if safe.exists():
        return jsonify({"ok": False, "error": "File already exists"}), 409
    safe.parent.mkdir(parents=True, exist_ok=True)
    safe.write_text(f"# {Path(file).stem}\n\n", encoding="utf-8")
    return jsonify({"ok": True, "file": file})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file part"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"ok": False, "error": "Empty filename"}), 400
    data = f.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return jsonify({"ok": False, "error": "File exceeds 10 MB limit"}), 413
    ext = Path(f.filename).suffix.lower() or ""
    safe_name = uuid.uuid4().hex + ext
    (artifact_path / safe_name).write_bytes(data)
    return jsonify({"ok": True, "filename": safe_name, "original": f.filename})


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
  border-right: 1px solid var(--border); display: flex; flex-direction: column; z-index: 10; }
#main    { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

@media (max-width: 767px) {
  #sidebar { position: fixed; inset: 0 auto 0 0; transform: translateX(-100%);
    transition: transform .22s cubic-bezier(.4,0,.2,1); box-shadow: none; }
  #sidebar.show { transform: translateX(0); box-shadow: 8px 0 40px rgba(0,0,0,.4); }
  #backdrop { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.4); z-index: 9; }
  #backdrop.show { display: block; }
}

/* ── Sidebar ── */
.sb-head { padding: 10px; border-bottom: 1px solid var(--border); }
.sb-brand { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700;
  color: var(--text); padding: 2px 0 10px; }

#search { width: 100%; background: var(--panel2); color: var(--text); border: 1px solid var(--border);
  border-radius: var(--r-sm); padding: 6px 10px 6px 30px; font-size: 12.5px; outline: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='13' height='13' viewBox='0 0 24 24' fill='none' stroke='%237e9ab5' stroke-width='2'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='m21 21-4.35-4.35'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: 9px center;
  transition: border-color .15s; }
#search:focus { border-color: var(--accent); }
#search::placeholder { color: var(--muted); }

.sb-actions { display: flex; gap: 6px; margin-top: 8px; }

.btn-sb { flex: 1; padding: 5px 8px; font-size: 12px; font-weight: 600; border-radius: var(--r-sm);
  cursor: pointer; border: 1px solid var(--border); background: var(--panel2); color: var(--muted);
  display: flex; align-items: center; justify-content: center; gap: 5px; white-space: nowrap;
  transition: background .12s, color .12s, border-color .12s; }
.btn-sb:hover { background: var(--panel); border-color: var(--accent); color: var(--accent); }
.btn-sb.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-sb.primary:hover { background: var(--accent-h); }

#fileList { flex: 1; overflow-y: auto; padding: 4px 0; }

.folder-row { padding: 6px 10px; cursor: pointer; font-size: 11px; font-weight: 700;
  color: var(--muted); text-transform: uppercase; letter-spacing: .08em;
  display: flex; align-items: center; gap: 6px; user-select: none;
  transition: background .1s, color .1s; }
.folder-row:hover { background: var(--panel2); color: var(--text); }
.folder-chevron { font-size: 9px; transition: transform .15s; }
.folder-row.open .folder-chevron { transform: rotate(90deg); }
.folder-count { margin-left: auto; font-size: 10px; font-weight: 400; opacity: .5; }
.folder-children { display: none; }
.folder-children.open { display: block; }

.file-item { padding: 6px 10px 6px 26px; cursor: pointer; font-size: 12.5px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  border-left: 2px solid transparent; border-bottom: 1px solid var(--border-soft);
  transition: background .1s, color .1s, border-left-color .1s; }
.file-item.root { padding-left: 10px; }
.file-item:hover { background: var(--panel2); color: var(--text); }
.file-item.active { background: var(--accent-dim); border-left-color: var(--accent); color: var(--text); font-weight: 600; }
.file-item.readme { color: var(--accent); }
.folder-tag { font-size: 10px; color: var(--muted); opacity: .6; margin-right: 3px; }

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
#editorPane { flex: 1; display: flex; flex-direction: column;
  border-right: 1px solid var(--border); overflow: hidden; position: relative; }
#editorRenderedPane { flex: 1; overflow-y: auto; padding: 20px; background: var(--bg); }

@media (max-width: 767px) {
  #editorWrap { flex-direction: column; }
  #editorPane { flex: 1; border-right: none; border-bottom: 1px solid var(--border); }
  #editorRenderedPane { flex: 1; }
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

/* Modals */
.modal-bg { position: fixed; inset: 0; z-index: 1000; display: none; align-items: center;
  justify-content: center; background: rgba(0,0,0,.5); }
.modal-bg.show { display: flex; }
.modal-box { background: var(--panel); border: 1px solid var(--border); border-radius: var(--r-lg);
  padding: 20px; width: 400px; max-width: 90vw; box-shadow: var(--shadow); }
.modal-box h5 { font-size: 14px; font-weight: 700; margin-bottom: 12px; }
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

.modal-warn { display: flex; gap: 9px; background: rgba(245,158,11,.1);
  border: 1px solid rgba(245,158,11,.3); border-radius: var(--r-sm);
  padding: 9px 11px; margin-bottom: 12px; font-size: 12.5px; line-height: 1.5; color: var(--body); }
.modal-warn i { color: var(--warn); font-size: 15px; flex-shrink: 0; margin-top: 1px; }

.spinner { display: inline-block; width: 12px; height: 12px;
  border: 2px solid rgba(255,255,255,.3); border-top-color: #fff;
  border-radius: 50%; animation: spin .6s linear infinite; margin-right: 5px; vertical-align: -2px; }
@keyframes spin { to { transform: rotate(360deg); } }
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
          <i class="bi bi-arrow-clockwise"></i> Refresh
        </button>
        <button class="btn-sb primary" onclick="openNewModal()">
          <i class="bi bi-plus-lg"></i> New
        </button>
        <button class="btn-sb d-md-none" onclick="closeSidebar()">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>
    </div>
    <div id="fileList"></div>
  </aside>

  <!-- Main -->
  <div id="main">
    <div id="topbar">
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
            <span>Drop media here · max 10 MB</span>
          </div>
        </div>
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

<!-- New File Modal -->
<div class="modal-bg" id="newModal" onclick="if(event.target===this)closeNewModal()">
  <div class="modal-box">
    <h5><i class="bi bi-file-earmark-plus me-2"></i>New file</h5>
    <input class="modal-input" id="newFilename" placeholder="e.g. notes/my-paper.md"
      onkeydown="if(event.key==='Enter')confirmNew(); if(event.key==='Escape')closeNewModal()">
    <div class="modal-hint">Relative path from library root. <code>.md</code> added automatically.</div>
    <div class="modal-error" id="newError"></div>
    <div class="modal-footer">
      <button class="btn-modal" onclick="closeNewModal()">Cancel</button>
      <button class="btn-modal primary" onclick="confirmNew()">Create</button>
    </div>
  </div>
</div>

<!-- Media warning modal (for export) -->
<div class="modal-bg" id="exportWarnModal" onclick="if(event.target===this)closeExportWarnModal()">
  <div class="modal-box">
    <h5><i class="bi bi-exclamation-triangle me-2"></i>Media in this document</h5>
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
let treeData   = { root_files: [], folders: [] };
let openFolders= new Set();
let current    = null;
let mode       = 'preview';
let isDirty    = false;
let previewDebounce = null;
let theme      = 'dark';
let syncLock   = false;

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

// ── Sidebar (mobile) ──────────────────────────────────────
function openSidebar()  {
  document.getElementById('sidebar').classList.add('show');
  document.getElementById('backdrop').classList.add('show');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('show');
  document.getElementById('backdrop').classList.remove('show');
}

// ── File list ─────────────────────────────────────────────
async function loadFiles() {
  const res = await fetch('/api/files');
  treeData = await res.json();
  renderTree();
}

function makeFileItem(file, isRoot) {
  const div = document.createElement('div');
  const isReadme = file.toLowerCase() === 'readme.md';
  const name = file.split('/').pop().replace(/\.md$/i, '');
  div.className = 'file-item' + (isRoot ? ' root' : '') + (file === current ? ' active' : '') + (isReadme ? ' readme' : '');
  div.title = file;
  if (isReadme) {
    div.innerHTML = `<i class="bi bi-bookmark-star-fill" style="font-size:10px;margin-right:4px"></i>${name}`;
  } else {
    div.textContent = name;
  }
  div.onclick = () => { openFile(file, true); closeSidebar(); };
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
    const row = document.createElement('div');
    row.className = 'folder-row' + (isOpen ? ' open' : '');
    row.innerHTML = `<i class="bi bi-chevron-right folder-chevron"></i>
      <i class="bi bi-folder${isOpen ? '2-open' : ''}-fill" style="font-size:11px;opacity:.55"></i>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${folder.name}</span>
      <span class="folder-count">${folder.files.length}</span>`;
    row.onclick = () => toggleFolder(folder.name);

    const children = document.createElement('div');
    children.className = 'folder-children' + (isOpen ? ' open' : '');
    folder.files.forEach(f => children.appendChild(makeFileItem(f, false)));
    list.appendChild(row);
    list.appendChild(children);
  });
}

function toggleFolder(name) {
  openFolders.has(name) ? openFolders.delete(name) : openFolders.add(name);
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
    if (folder) {
      div.innerHTML = `<span class="folder-tag">${folder}/</span>${name}`;
    } else if (isReadme) {
      div.innerHTML = `<i class="bi bi-bookmark-star-fill" style="font-size:10px;margin-right:4px"></i>${name}`;
    } else {
      div.textContent = name;
    }
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

// ── Media upload ──────────────────────────────────────────
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

// ── New file modal ─────────────────────────────────────────
function openNewModal() {
  document.getElementById('newFilename').value = '';
  document.getElementById('newError').style.display = 'none';
  document.getElementById('newModal').classList.add('show');
  setTimeout(() => document.getElementById('newFilename').focus(), 50);
}
function closeNewModal() { document.getElementById('newModal').classList.remove('show'); }
async function confirmNew() {
  const name = document.getElementById('newFilename').value.trim();
  const err  = document.getElementById('newError');
  if (!name) { err.textContent = 'Enter a filename.'; err.style.display = 'block'; return; }
  const res  = await fetch('/api/new', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: name }),
  });
  const data = await res.json();
  if (!data.ok) { err.textContent = data.error; err.style.display = 'block'; return; }
  closeNewModal();
  await loadFiles();
  setMode('editor');
  await openFile(data.file, true);
}

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
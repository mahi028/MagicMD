from flask import Flask, request, jsonify, Response, redirect
import json
from pathlib import Path
import markdown
import html
import re
import os
import uuid
import mimetypes

# ============================================================
# CONFIG
# ============================================================

MARKDOWN_FOLDER = r"/home/mohittewari/papers/papers/summary"  # CHANGE THIS
ARTIFACT_DIR    = r"/home/mohittewari/papers/artifacts"  # CHANGE THIS
HOST = "0.0.0.0"
PORT = 8890
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB

# ============================================================
# SETUP
# ============================================================

app = Flask(__name__)
base_path     = Path(MARKDOWN_FOLDER).resolve()
artifact_path = Path(ARTIFACT_DIR).resolve()
artifact_path.mkdir(parents=True, exist_ok=True)


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
    safe_path = (base_path / relative_path).resolve()
    if not str(safe_path).startswith(str(base_path)):
        return None
    return safe_path


def read_markdown(relative_path):
    safe_path = resolve_safe_path(relative_path)
    if safe_path is None or not safe_path.exists():
        return None
    return safe_path.read_text(encoding="utf-8")


def write_markdown(relative_path, content):
    safe_path = resolve_safe_path(relative_path)
    if safe_path is None:
        return False
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content, encoding="utf-8")
    return True


def get_readme():
    readme = base_path / "README.md"
    if readme.exists():
        return "README.md"
    for f in base_path.iterdir():
        if f.name.lower() == "readme.md":
            return f.name
    return None


def render_markdown(md_text):
    math_stash = {}
    counter = [0]

    def stash(expr):
        key = f"MATHSTASH{counter[0]}END"
        math_stash[key] = expr
        counter[0] += 1
        return key

    md_text = re.sub(
        r"\$\$(.+?)\$\$",
        lambda m: stash(f'<span class="math-display">\\({m.group(1).strip()}\\)</span>'),
        md_text,
        flags=re.DOTALL,
    )
    md_text = re.sub(
        r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
        lambda m: stash(f'<span class="math-inline">\\({m.group(1).strip()}\\)</span>'),
        md_text,
    )

    def mermaid_replacer(match):
        code = match.group(1).strip()
        return f'\n<div class="mermaid">\n{html.escape(code)}\n</div>\n'

    md_text = re.sub(
        r"```mermaid\s*\n(.*?)```",
        mermaid_replacer,
        md_text,
        flags=re.DOTALL,
    )

    result = markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "toc", "nl2br"],
    )

    for key, val in math_stash.items():
        result = result.replace(f"<p>{key}</p>", val)
        result = result.replace(key, val)

    return result


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    file_param = request.args.get("file", "").strip()
    if file_param and not file_param.lower().endswith(".md"):
        return Response("Only .md files are allowed.", status=400, mimetype="text/plain")
    readme = get_readme()
    initial_file = json.dumps(file_param or readme or "")
    return Response(HTML.replace("__INITIAL_FILE__", initial_file),
                    mimetype="text/html; charset=utf-8")


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
    root_files = []
    folder_map = {}

    for f in flat:
        parts = f.split("/")
        if len(parts) == 1:
            root_files.append(f)
        else:
            folder = parts[0]
            folder_map.setdefault(folder, []).append(f)

    folders = [
        {"name": name, "files": files}
        for name, files in sorted(folder_map.items())
    ]
    return jsonify({"root_files": root_files, "folders": folders})


@app.route("/api/readme")
def api_readme():
    readme = get_readme()
    return jsonify({"readme": readme})


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
    md_text = data.get("markdown", "")
    return Response(render_markdown(md_text), mimetype="text/html; charset=utf-8")


@app.route("/api/save", methods=["POST"])
def api_save():
    data = request.get_json(force=True)
    file = data.get("file", "").strip()
    content = data.get("content", "")
    if not file:
        return jsonify({"ok": False, "error": "No file specified"}), 400
    if not file.lower().endswith(".md"):
        return jsonify({"ok": False, "error": "Only .md files are allowed"}), 400
    ok = write_markdown(file, content)
    if not ok:
        return jsonify({"ok": False, "error": "Path traversal blocked"}), 403
    return jsonify({"ok": True})


@app.route("/api/new", methods=["POST"])
def api_new():
    data = request.get_json(force=True)
    file = data.get("file", "").strip()
    if not file:
        return jsonify({"ok": False, "error": "No filename"}), 400
    if not file.endswith(".md"):
        file += ".md"
    safe_path = resolve_safe_path(file)
    if safe_path is None:
        return jsonify({"ok": False, "error": "Invalid path"}), 403
    if safe_path.exists():
        return jsonify({"ok": False, "error": "File already exists"}), 409
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(f"# {Path(file).stem}\n\n", encoding="utf-8")
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
    dest = artifact_path / safe_name
    dest.write_bytes(data)

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

<!-- Bootstrap 5 -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>

<!-- Bootstrap Icons -->
<link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css" rel="stylesheet">

<!-- Highlight.js -->
<link id="hlDark"  rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">
<link id="hlLight" rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github.min.css" disabled>
<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>

<!-- MathJax -->
<script>
MathJax = {
  tex: { inlineMath: [['\\(', '\\)']], displayMath: [['\\[', '\\]']] },
  options: { skipHtmlTags: ['script','noscript','style','textarea','pre'] },
  startup: { typeset: false }
};
</script>
<script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"></script>

<style>
/* ═══════════════════════════════════════
   TOKENS — dark (default)
═══════════════════════════════════════ */
:root {
  --bg-base:      #0c1220;
  --bg-panel:     #111827;
  --bg-panel-2:   #1a2538;
  --bg-article:   #111827;
  --border:       #1e3148;
  --border-soft:  rgba(30,49,72,.6);
  --text-primary: #e2e8f0;
  --text-muted:   #7e9ab5;
  --text-body:    #c8d4e3;
  --accent:       #3b82f6;
  --accent-hover: #60a5fa;
  --accent-dim:   rgba(59,130,246,.12);
  --danger:       #ef4444;
  --success:      #22c55e;
  --warning:      #f59e0b;
  --sidebar-w:    288px;
  --topbar-h:     50px;
  --code-bg:      #0a1120;
  --radius-sm:    6px;
  --radius-md:    10px;
  --radius-lg:    14px;
  --shadow-sm:    0 2px 8px rgba(0,0,0,.25);
  --shadow-lg:    0 12px 40px rgba(0,0,0,.4);
  --scrollbar:    #1f3a56;
}

/* TOKENS — light */
[data-theme="light"] {
  --bg-base:      #f0f4f8;
  --bg-panel:     #ffffff;
  --bg-panel-2:   #e8edf5;
  --bg-article:   #ffffff;
  --border:       #d0dbe8;
  --border-soft:  rgba(200,215,230,.7);
  --text-primary: #0f172a;
  --text-muted:   #5a7490;
  --text-body:    #334155;
  --accent:       #2563eb;
  --accent-hover: #1d4ed8;
  --accent-dim:   rgba(37,99,235,.08);
  --code-bg:      #f1f5f9;
  --shadow-sm:    0 2px 8px rgba(0,0,0,.08);
  --shadow-lg:    0 8px 24px rgba(0,0,0,.12);
  --scrollbar:    #b0c2d6;
}

*, *::before, *::after { box-sizing: border-box; }

html, body {
  height: 100%; overflow: hidden;
  background: var(--bg-base);
  color: var(--text-primary);
  font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  transition: background .2s, color .2s;
}

/* ── Shell ── */
#app     { display: flex; height: 100vh; }
#sidebar {
  width: var(--sidebar-w); min-width: var(--sidebar-w);
  background: var(--bg-panel);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  transition: transform .25s ease, background .2s;
  z-index: 10;
}
#main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

@media (max-width: 767.98px) {
  #sidebar { position: fixed; top: 0; left: 0; bottom: 0; transform: translateX(-100%); }
  #sidebar.show { transform: translateX(0); box-shadow: 6px 0 40px rgba(0,0,0,.45); }
  #sidebarBackdrop { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.45); z-index: 9; }
  #sidebarBackdrop.show { display: block; }
}

/* ── Sidebar ── */
.sidebar-head {
  padding: 12px;
  border-bottom: 1px solid var(--border);
}
.sidebar-brand {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; font-weight: 700; letter-spacing: -.01em;
  color: var(--text-primary); margin-bottom: 10px;
}

#search {
  width: 100%;
  background: var(--bg-panel-2);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 6px 10px 6px 32px;
  font-size: 12.5px;
  outline: none;
  transition: border-color .15s;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%237e9ab5' stroke-width='2'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='m21 21-4.35-4.35'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: 9px center;
}
#search:focus { border-color: var(--accent); }
#search::placeholder { color: var(--text-muted); }

.sidebar-actions { display: flex; gap: 6px; margin-top: 8px; }

.btn-side {
  flex: 1; padding: 5px 10px; font-size: 12px; font-weight: 600;
  border-radius: var(--radius-sm); cursor: pointer; border: 1px solid var(--border);
  background: var(--bg-panel-2); color: var(--text-muted);
  transition: background .12s, border-color .12s, color .12s;
  display: flex; align-items: center; justify-content: center; gap: 5px;
  white-space: nowrap;
}
.btn-side:hover { background: var(--bg-panel); border-color: var(--accent); color: var(--accent); }
.btn-side.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-side.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); color: #fff; }

#fileList { flex: 1; overflow-y: auto; padding: 4px 0; }

.folder-row {
  padding: 7px 12px;
  cursor: pointer;
  font-size: 11px; font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .09em;
  display: flex; align-items: center; gap: 6px;
  user-select: none;
  transition: background .12s, color .12s;
}
.folder-row:hover { background: var(--bg-panel-2); color: var(--text-primary); }
.folder-row .folder-chevron { font-size: 9px; transition: transform .18s; flex-shrink: 0; }
.folder-row.open .folder-chevron { transform: rotate(90deg); }
.folder-row .folder-count { margin-left: auto; font-size: 10px; font-weight: 400; opacity: .55; }

.folder-children { display: none; }
.folder-children.open { display: block; }

.file-item {
  padding: 7px 12px 7px 28px;
  cursor: pointer;
  font-size: 12.5px;
  color: var(--text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  transition: background .1s, color .1s, border-left-color .1s;
  border-left: 2px solid transparent;
  border-bottom: 1px solid var(--border-soft);
}
.file-item.root { padding-left: 12px; }
.file-item:hover  { background: var(--bg-panel-2); color: var(--text-primary); }
.file-item.active {
  background: var(--accent-dim);
  border-left-color: var(--accent);
  color: var(--text-primary);
  font-weight: 600;
}
.file-item.readme { color: var(--accent); }
.file-folder-tag { font-size: 10px; color: var(--text-muted); margin-right: 4px; opacity: .65; }

/* ── Topbar ── */
#topbar {
  height: var(--topbar-h);
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
  padding: 0 12px; flex-shrink: 0;
}
#topbarTitle {
  flex: 1; font-size: 12.5px; color: var(--text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
}
#topbarTitle .paper-name { color: var(--text-primary); font-weight: 600; font-size: 13px; }

#saveStatus { font-size: 11.5px; white-space: nowrap; font-weight: 600; }
#saveStatus.saved   { color: var(--success); }
#saveStatus.unsaved { color: var(--warning); }
#saveStatus.error   { color: var(--danger); }

.icon-btn {
  width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center;
  border-radius: var(--radius-sm); border: 1px solid var(--border);
  background: var(--bg-panel-2); color: var(--text-muted);
  cursor: pointer; font-size: 15px; flex-shrink: 0;
  transition: background .12s, color .12s, border-color .12s;
}
.icon-btn:hover { background: var(--bg-panel); color: var(--accent); border-color: var(--accent); }

.tab-group {
  display: flex; border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: hidden; flex-shrink: 0;
}
.tab-btn {
  padding: 5px 12px; font-size: 12px; font-weight: 600; cursor: pointer;
  background: var(--bg-panel-2); color: var(--text-muted);
  border: none; outline: none;
  transition: background .12s, color .12s;
}
.tab-btn + .tab-btn { border-left: 1px solid var(--border); }
.tab-btn.active { background: var(--accent); color: #fff; }
.tab-btn:hover:not(.active) { background: var(--bg-panel); color: var(--text-primary); }

.btn-save {
  padding: 5px 12px; font-size: 12px; font-weight: 600;
  background: var(--success); color: #fff; border: none; border-radius: var(--radius-sm);
  cursor: pointer; display: none; gap: 5px; align-items: center;
  transition: background .12s;
}
.btn-save:hover { filter: brightness(1.1); }

/* ── Workspace ── */
#workspace { flex: 1; display: flex; overflow: hidden; }

/* ── Preview mode: rendered only ── */
#previewWrap {
  flex: 1; display: flex; overflow: hidden;
}
#previewRendered {
  flex: 1; overflow-y: auto; padding: 20px; background: var(--bg-base);
}

/* ── Editor mode: raw left + rendered right ── */
#editorWrap { flex: 1; display: flex; overflow: hidden; }

#editorPane {
  flex: 1; display: flex; flex-direction: column;
  border-right: 1px solid var(--border); overflow: hidden; position: relative;
}

#editorRenderedPane {
  flex: 1; overflow-y: auto; padding: 20px; background: var(--bg-base);
}

@media (max-width: 767.98px) {
  #editorWrap  { flex-direction: column; }
  #editorPane  { flex: 1; border-right: none; border-bottom: 1px solid var(--border); }
  #editorRenderedPane { flex: 1; }
}

.article {
  max-width: 820px; margin: 0 auto;
  background: var(--bg-article);
  border-radius: var(--radius-lg);
  padding: 2.25rem 2.75rem;
  border: 1px solid var(--border);
  box-shadow: var(--shadow-lg);
}
@media (max-width: 575.98px) {
  #previewRendered { padding: .75rem; }
  #editorRenderedPane { padding: .75rem; }
  .article { padding: 1.25rem 1rem; border-radius: var(--radius-md); }
}

.pane-label {
  padding: 5px 12px; font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 1.2px;
  color: var(--text-muted);
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border); flex-shrink: 0;
  display: flex; align-items: center; justify-content: space-between;
}

#editor {
  flex: 1; resize: none;
  background: var(--code-bg); color: var(--text-primary);
  font-family: 'JetBrains Mono', Consolas, 'Fira Code', monospace;
  font-size: 13px; line-height: 1.75;
  padding: 1rem 1.25rem;
  border: none; outline: none; tab-size: 4;
  transition: background .2s, color .2s;
}

/* ── Drop zone overlay ── */
#dropOverlay {
  display: none; position: absolute; inset: 0; z-index: 50;
  background: rgba(59,130,246,.15);
  border: 2px dashed var(--accent);
  border-radius: var(--radius-md);
  align-items: center; justify-content: center;
  flex-direction: column; gap: 8px;
  color: var(--accent); font-weight: 600; font-size: 14px;
  pointer-events: none;
}
#dropOverlay i { font-size: 2.5rem; opacity: .8; }
#dropOverlay.active { display: flex; }

/* ── Upload progress toast ── */
#uploadToast {
  position: fixed; bottom: 20px; right: 20px; z-index: 200;
  background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: var(--radius-md); padding: 10px 14px;
  font-size: 12.5px; color: var(--text-primary);
  box-shadow: var(--shadow-lg);
  display: none; align-items: center; gap: 10px; min-width: 220px;
}
#uploadToast.show { display: flex; }
#uploadToast .toast-bar {
  flex: 1; height: 4px; background: var(--bg-panel-2); border-radius: 999px; overflow: hidden;
}
#uploadToast .toast-fill { height: 100%; background: var(--accent); width: 0; transition: width .2s; border-radius: 999px; }
#uploadToast .toast-msg { white-space: nowrap; }

/* ── Markdown prose ── */
.article h1, .article h2, .article h3, .article h4 { color: var(--text-primary); font-weight: 700; }
.article h1 { font-size: 1.85rem; margin-bottom: .35em; line-height: 1.25; }
.article h2 { font-size: 1.35rem; margin: 1.4em 0 .35em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
.article h3 { font-size: 1.1rem; margin: 1.1em 0 .25em; }
.article p, .article li { line-height: 1.85; color: var(--text-body); margin-bottom: .5em; }
.article code {
  font-family: 'JetBrains Mono', Consolas, monospace; font-size: .84em;
  background: var(--bg-panel-2); color: var(--accent-hover);
  padding: .15em .4em; border-radius: 4px; border: 1px solid var(--border);
}
.article pre {
  border-radius: var(--radius-md); overflow: auto; padding: 14px;
  background: var(--code-bg) !important; margin: 1em 0;
  border: 1px solid var(--border); box-shadow: var(--shadow-sm);
}
.article pre code { background: transparent; border: none; padding: 0; color: inherit; }
.article blockquote {
  border-left: 3px solid var(--accent); padding: .5em 1em;
  color: var(--text-muted); margin: 1em 0;
  background: var(--bg-panel-2); border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
}
.article table { width: 100%; border-collapse: collapse; margin: 1em 0; font-size: .93em; }
.article th, .article td { border: 1px solid var(--border); padding: 8px 12px; }
.article th { background: var(--bg-panel-2); color: var(--text-primary); font-weight: 600; }
.article tr:nth-child(even) td { background: rgba(128,128,128,.04); }
.article a { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.article a:hover { color: var(--accent-hover); }
.article img { max-width: 100%; border-radius: var(--radius-md); display: block; margin: 1em auto; }
.article hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
.article video, .article audio {
  display: block; max-width: 100%; margin: 1em auto;
  border-radius: var(--radius-md); border: 1px solid var(--border);
}
.article video { background: #000; }

.math-display { display: block; text-align: center; margin: 1.4em 0; overflow-x: auto; }
.mermaid {
  border-radius: var(--radius-md); padding: 16px; overflow: auto; margin: 1.2em 0;
  background: #1e1e2e; border: 1px solid var(--border);
}
[data-theme="light"] .mermaid { background: #f8fafc; }

.empty { text-align: center; color: var(--text-muted); padding: 5rem 0; font-size: 15px; }
.empty i { font-size: 2.5rem; display: block; margin-bottom: 12px; opacity: .35; }
.empty p { font-size: 13px; margin-top: 4px; }

.article-footer {
  margin-top: 2.5rem; padding-top: 1rem;
  border-top: 1px solid var(--border);
  font-size: 11px; color: var(--text-muted);
  display: flex; justify-content: space-between; align-items: center;
}

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 999px; }
::-webkit-scrollbar-track { background: transparent; }

/* ── Modal ── */
.modal-custom {
  position: fixed; inset: 0; z-index: 1000;
  display: none; align-items: center; justify-content: center;
  background: rgba(0,0,0,.55);
}
.modal-custom.show { display: flex; }
.modal-box {
  background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 22px; width: 420px; max-width: 90vw;
  box-shadow: var(--shadow-lg);
}
.modal-box h5 { margin: 0 0 14px; font-size: 14.5px; color: var(--text-primary); font-weight: 700; }
.modal-input {
  width: 100%; background: var(--bg-panel-2); color: var(--text-primary);
  border: 1px solid var(--border); border-radius: var(--radius-sm);
  padding: 8px 12px; font-size: 13px; outline: none;
  transition: border-color .15s;
}
.modal-input:focus { border-color: var(--accent); }
.modal-hint { font-size: 11.5px; color: var(--text-muted); margin-top: 6px; }
.modal-error { font-size: 12px; color: var(--danger); margin-top: 6px; display: none; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.btn-modal {
  padding: 7px 16px; font-size: 13px; font-weight: 600; border-radius: var(--radius-sm);
  border: 1px solid var(--border); cursor: pointer;
  background: var(--bg-panel-2); color: var(--text-primary);
  transition: background .12s;
}
.btn-modal:hover { background: var(--bg-panel); }
.btn-modal.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-modal.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }

.sync-badge {
  font-size: 10px; font-weight: 600; padding: 2px 7px;
  border-radius: 999px; cursor: pointer; letter-spacing: .03em;
  border: 1px solid var(--border);
  background: var(--bg-panel-2); color: var(--text-muted);
  transition: background .12s, color .12s;
  user-select: none;
}
.sync-badge.on { background: var(--accent-dim); color: var(--accent); border-color: var(--accent); }
</style>
</head>
<body>
<div id="app">

  <div id="sidebarBackdrop" onclick="closeSidebar()"></div>

  <!-- ── Sidebar ── -->
  <aside id="sidebar">
    <div class="sidebar-head">
      <div class="sidebar-brand">
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
      <div class="sidebar-actions">
        <button class="btn-side" onclick="loadFiles()" title="Refresh">
          <i class="bi bi-arrow-clockwise"></i> Refresh
        </button>
        <button class="btn-side primary" onclick="openNewModal()">
          <i class="bi bi-plus-lg"></i> New
        </button>
        <button class="btn-side d-md-none" onclick="closeSidebar()">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>
    </div>
    <div id="fileList"></div>
  </aside>

  <!-- ── Main ── -->
  <div id="main">
    <!-- Topbar -->
    <div id="topbar">
      <button class="icon-btn d-md-none" onclick="openSidebar()">
        <i class="bi bi-list" style="font-size:17px"></i>
      </button>

      <span id="topbarTitle">
        <span style="color:var(--text-muted);font-size:12.5px">No file selected</span>
      </span>

      <div class="tab-group ms-auto">
        <button class="tab-btn active" id="tabPreview" onclick="setMode('preview')">
          <i class="bi bi-eye me-1"></i><span class="d-none d-sm-inline">Preview</span>
        </button>
        <button class="tab-btn" id="tabEditor" onclick="setMode('editor')">
          <i class="bi bi-pencil me-1"></i><span class="d-none d-sm-inline">Edit</span>
        </button>
      </div>

      <button class="btn-save" id="saveBtn" onclick="saveFile()">
        <i class="bi bi-floppy"></i><span class="d-none d-sm-inline">Save</span>
      </button>
      <span id="saveStatus"></span>

      <button class="icon-btn" onclick="toggleTheme()" title="Toggle theme" id="themeToggle">
        <i class="bi bi-sun-fill" id="themeIcon"></i>
      </button>
    </div>

    <!-- Workspace -->
    <div id="workspace">

      <!-- Preview mode: rendered article only -->
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

      <!-- Editor mode: raw markdown left, live preview right, synced scroll -->
      <div id="editorWrap" style="display:none">
        <div id="editorPane">
          <div class="pane-label">
            <span>Markdown <kbd style="font-size:9px;background:var(--bg-panel-2);border:1px solid var(--border);border-radius:3px;padding:1px 5px;color:var(--text-muted)">Ctrl+S</kbd></span>
            <span class="sync-badge on" id="uploadHint" title="Drag &amp; drop media files here">
              <i class="bi bi-cloud-upload" style="font-size:10px;margin-right:3px"></i>Drop media
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
<div id="uploadToast">
  <span class="toast-msg" id="toastMsg">Uploading…</span>
  <div class="toast-bar"><div class="toast-fill" id="toastFill"></div></div>
</div>

<!-- New File Modal -->
<div class="modal-custom" id="newFileModal" onclick="if(event.target===this)closeNewModal()">
  <div class="modal-box">
    <h5><i class="bi bi-file-earmark-plus me-2"></i>New file</h5>
    <input class="modal-input" id="newFilename"
      placeholder="e.g. notes/my-paper.md"
      onkeydown="if(event.key==='Enter')confirmNew(); if(event.key==='Escape')closeNewModal()">
    <div class="modal-hint">Path relative to library root. <code>.md</code> added automatically.</div>
    <div class="modal-error" id="newError"></div>
    <div class="modal-footer">
      <button class="btn-modal" onclick="closeNewModal()">Cancel</button>
      <button class="btn-modal primary" onclick="confirmNew()">Create</button>
    </div>
  </div>
</div>

<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
window.mermaid = mermaid;
window.mermaidReady = true;
</script>

<script>
// ── State ──────────────────────────────────────────────
let treeData        = { root_files: [], folders: [] };
let openFolders     = new Set();
let current         = null;
let mode            = 'preview';
let isDirty         = false;
let previewDebounce = null;
let theme           = 'dark';
let syncScroll      = true;
let syncLock        = false;

// ══════════════════════════════════════════════════
//  THEME
// ══════════════════════════════════════════════════
function mermaidTheme(t) { return t === 'dark' ? 'dark' : 'default'; }

function applyTheme(t) {
  theme = t;
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('themeIcon').className = t === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
  document.getElementById('hlDark' ).disabled = (t === 'light');
  document.getElementById('hlLight').disabled = (t === 'dark');
  document.querySelectorAll('pre code').forEach(b => {
    b.removeAttribute('data-highlighted');
    hljs.highlightElement(b);
  });
  if (window.mermaid) {
    window.mermaid.initialize({ startOnLoad: false, theme: mermaidTheme(t), securityLevel: 'loose' });
    document.querySelectorAll('.mermaid[data-src]').forEach(async (el) => {
      el.removeAttribute('data-processed');
      el.innerHTML = el.dataset.src;
      try { await window.mermaid.run({ nodes: [el] }); } catch(e) {}
    });
  }
  localStorage.setItem('md-theme', t);
}

function toggleTheme() { applyTheme(theme === 'dark' ? 'light' : 'dark'); }

// ══════════════════════════════════════════════════
//  URL HELPERS
// ══════════════════════════════════════════════════
function getUrlFile() {
  return new URLSearchParams(window.location.search).get('file') || '';
}
function setUrlFile(file) {
  const url = new URL(window.location.href);
  file ? url.searchParams.set('file', file) : url.searchParams.delete('file');
  history.pushState({ file }, '', url.toString());
}
function slugTitle(file) {
  if (!file) return '';
  return file.split('/').pop().replace(/\.md$/i, '').replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// ══════════════════════════════════════════════════
//  SIDEBAR (mobile)
// ══════════════════════════════════════════════════
function openSidebar()  {
  document.getElementById('sidebar').classList.add('show');
  document.getElementById('sidebarBackdrop').classList.add('show');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('show');
  document.getElementById('sidebarBackdrop').classList.remove('show');
}

// ══════════════════════════════════════════════════
//  FILE LIST
// ══════════════════════════════════════════════════
async function loadFiles() {
  const res = await fetch('/api/files');
  treeData = await res.json();
  renderTree();
}

function makeFileItem(file, isRoot) {
  const div = document.createElement('div');
  const isReadme = file.toLowerCase() === 'readme.md';
  const name = file.split('/').pop().replace(/\.md$/i, '');
  div.className = 'file-item'
    + (isRoot ? ' root' : '')
    + (file === current ? ' active' : '')
    + (isReadme ? ' readme' : '');
  div.title = file;
  if (isReadme) {
    div.innerHTML = `<i class="bi bi-bookmark-star-fill" style="font-size:10px;margin-right:5px"></i>${name}`;
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
    row.innerHTML = `
      <i class="bi bi-chevron-right folder-chevron"></i>
      <i class="bi bi-folder${isOpen ? '2-open' : ''}-fill" style="font-size:11px;opacity:.6"></i>
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

  const allFlat = [...treeData.root_files, ...treeData.folders.flatMap(f => f.files)];
  const matched = allFlat.filter(f => f.toLowerCase().includes(q));
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
      div.innerHTML = `<span class="file-folder-tag">${folder}/</span>${name}`;
    } else if (isReadme) {
      div.innerHTML = `<i class="bi bi-bookmark-star-fill" style="font-size:10px;margin-right:5px"></i>${name}`;
    } else {
      div.textContent = name;
    }
    div.onclick = () => { openFile(file, true); closeSidebar(); };
    list.appendChild(div);
  });

  if (!matched.length) {
    list.innerHTML = `<div style="padding:20px 14px;font-size:12px;color:var(--text-muted);text-align:center">No results</div>`;
  }
}

// ══════════════════════════════════════════════════
//  OPEN FILE
// ══════════════════════════════════════════════════
async function openFile(file, updateUrl) {
  if (!file) return;
  if (isDirty && current) {
    if (!confirm(`Discard unsaved changes in "${current}"?`)) return;
  }
  current = file;
  isDirty = false;
  updateSaveStatus('');

  if (updateUrl) setUrlFile(file);

  const parts = file.split('/');
  if (parts.length > 1) openFolders.add(parts[0]);
  renderTree();

  const prettyName = slugTitle(file);
  document.getElementById('topbarTitle').innerHTML =
    `<span class="paper-name">${prettyName}</span>
     <span style="color:var(--text-muted);font-size:11px;margin-left:6px">${file}</span>`;
  document.getElementById('saveBtn').style.display = 'none';
  document.title = `${prettyName} — Markdown Library`;

  const res = await fetch('/api/read?file=' + encodeURIComponent(file));
  if (!res.ok) { alert('Could not load file.'); return; }
  const md = await res.text();
  document.getElementById('editor').value = md;

  await Promise.all([
    renderInto('article', md),
    renderInto('editorPreview', md),
  ]);

  setupSyncScroll();
}

// ══════════════════════════════════════════════════
//  RENDER
// ══════════════════════════════════════════════════
async function renderInto(targetId, md) {
  const res = await fetch('/api/render', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ markdown: md }),
  });
  const htmlText = await res.text();
  const el = document.getElementById(targetId);
  el.innerHTML = htmlText;

  const footer = document.createElement('div');
  footer.className = 'article-footer';
  footer.innerHTML = `<span style="font-family:monospace;font-size:10.5px">${current || ''}</span>
    <span>${new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}</span>`;
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

// ══════════════════════════════════════════════════
//  SYNCED SCROLL (editor mode: textarea <-> rendered)
// ══════════════════════════════════════════════════
function setupSyncScroll() {
  const src  = document.getElementById('editor');
  const rend = document.getElementById('editorRenderedPane');

  src._syncHandler  && src.removeEventListener('scroll',  src._syncHandler);
  rend._syncHandler && rend.removeEventListener('scroll', rend._syncHandler);

  src._syncHandler = () => {
    if (!syncScroll || syncLock) return;
    syncLock = true;
    const ratio = src.scrollTop / Math.max(1, src.scrollHeight - src.clientHeight);
    rend.scrollTop = ratio * (rend.scrollHeight - rend.clientHeight);
    requestAnimationFrame(() => { syncLock = false; });
  };
  rend._syncHandler = () => {
    if (!syncScroll || syncLock) return;
    syncLock = true;
    const ratio = rend.scrollTop / Math.max(1, rend.scrollHeight - rend.clientHeight);
    src.scrollTop = ratio * (src.scrollHeight - src.clientHeight);
    requestAnimationFrame(() => { syncLock = false; });
  };

  src.addEventListener('scroll',  src._syncHandler,  { passive: true });
  rend.addEventListener('scroll', rend._syncHandler, { passive: true });
}

// ══════════════════════════════════════════════════
//  MODE
// ══════════════════════════════════════════════════
function setMode(m) {
  mode = m;
  document.getElementById('previewWrap').style.display = m === 'preview' ? 'flex' : 'none';
  document.getElementById('editorWrap' ).style.display = m === 'editor'  ? 'flex' : 'none';
  document.getElementById('saveBtn'    ).style.display = m === 'editor'  ? 'flex' : 'none';
  document.getElementById('tabPreview').classList.toggle('active', m === 'preview');
  document.getElementById('tabEditor' ).classList.toggle('active', m === 'editor');
}

// ══════════════════════════════════════════════════
//  EDITOR
// ══════════════════════════════════════════════════
function onEditorInput() {
  if (!isDirty) {
    isDirty = true;
    updateSaveStatus('unsaved');
    document.getElementById('saveBtn').style.display = 'flex';
  }
  clearTimeout(previewDebounce);
  previewDebounce = setTimeout(() => {
    const md = document.getElementById('editor').value;
    renderInto('editorPreview', md);
  }, 400);
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

// ══════════════════════════════════════════════════
//  SAVE
// ══════════════════════════════════════════════════
async function saveFile() {
  if (!current) return;
  const res = await fetch('/api/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: current, content: document.getElementById('editor').value }),
  });
  if (res.ok) {
    isDirty = false;
    updateSaveStatus('saved');
    setTimeout(() => updateSaveStatus(''), 2500);
  } else {
    updateSaveStatus('error');
  }
}

function updateSaveStatus(state) {
  const el = document.getElementById('saveStatus');
  el.className = state ? `small ${state}` : 'small';
  el.textContent = state === 'saved'   ? '✓ Saved'
                 : state === 'unsaved' ? '● Unsaved'
                 : state === 'error'   ? '✕ Failed'
                 : '';
}

// ══════════════════════════════════════════════════
//  MEDIA UPLOAD
// ══════════════════════════════════════════════════
function buildMediaMarkdown(url, originalName, mimeType) {
  const ext = originalName.split('.').pop().toLowerCase();
  const imageExts   = ['jpg','jpeg','png','gif','webp','svg','bmp','avif'];
  const videoExts   = ['mp4','webm','ogg','mov','mkv','avi'];
  const audioExts   = ['mp3','wav','ogg','aac','flac','m4a','opus'];

  if (imageExts.includes(ext)) return `![${originalName}](${url})`;
  if (videoExts.includes(ext)) return `<video controls src="${url}" title="${originalName}"></video>`;
  if (audioExts.includes(ext)) return `<audio controls src="${url}" title="${originalName}"></audio>`;
  return `[${originalName}](${url})`;
}

function insertAtCursor(text) {
  const ta = document.getElementById('editor');
  const s  = ta.selectionStart;
  const e  = ta.selectionEnd;
  const before = ta.value.substring(0, s);
  const after  = ta.value.substring(e);
  const prefix = (before.length && !before.endsWith('\n')) ? '\n' : '';
  const suffix = (after.length  && !after.startsWith('\n')) ? '\n' : '';
  ta.value = before + prefix + text + suffix + after;
  const newPos = s + prefix.length + text.length + suffix.length;
  ta.selectionStart = ta.selectionEnd = newPos;
  ta.focus();
  onEditorInput();
}

function showToast(msg, pct) {
  const t = document.getElementById('uploadToast');
  document.getElementById('toastMsg').textContent  = msg;
  document.getElementById('toastFill').style.width = pct + '%';
  t.classList.add('show');
}
function hideToast() {
  setTimeout(() => document.getElementById('uploadToast').classList.remove('show'), 1500);
}

async function uploadFile(file) {
  if (file.size > 10 * 1024 * 1024) {
    showToast('File exceeds 10 MB', 100);
    document.getElementById('toastFill').style.background = 'var(--danger)';
    hideToast();
    return;
  }
  showToast(`Uploading ${file.name}…`, 30);

  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/upload', { method: 'POST', body: fd });
  const data = await res.json();

  if (!data.ok) {
    showToast('Upload failed: ' + data.error, 100);
    document.getElementById('toastFill').style.background = 'var(--danger)';
    hideToast();
    return;
  }

  showToast(`Inserted ${file.name}`, 100);
  document.getElementById('toastFill').style.background = 'var(--success)';
  hideToast();

  const url = `/artifacts/${data.filename}`;
  const snippet = buildMediaMarkdown(url, data.original, file.type);
  insertAtCursor(snippet);
}

(function setupDrop() {
  const pane    = document.getElementById('editorPane');
  const overlay = document.getElementById('dropOverlay');
  let dragCount = 0;

  pane.addEventListener('dragenter', e => {
    e.preventDefault();
    if ([...e.dataTransfer.items].some(i => i.kind === 'file')) {
      dragCount++;
      overlay.classList.add('active');
    }
  });
  pane.addEventListener('dragleave', e => {
    dragCount--;
    if (dragCount <= 0) { dragCount = 0; overlay.classList.remove('active'); }
  });
  pane.addEventListener('dragover', e => e.preventDefault());
  pane.addEventListener('drop', async e => {
    e.preventDefault();
    dragCount = 0; overlay.classList.remove('active');
    if (!current) { alert('Open a file first to insert media.'); return; }
    if (mode !== 'editor') setMode('editor');
    const files = [...e.dataTransfer.files];
    for (const f of files) await uploadFile(f);
  });
})();

document.getElementById('editor').addEventListener('paste', async (e) => {
  const text = e.clipboardData.getData('text/plain').trim();
  if (!text) return;
  try {
    const url = new URL(text);
    if (!['http:', 'https:'].includes(url.protocol)) return;
    const ext = url.pathname.split('.').pop().toLowerCase().split('?')[0];
    const imageExts = ['jpg','jpeg','png','gif','webp','svg','bmp','avif'];
    const videoExts = ['mp4','webm','ogg','mov'];
    const audioExts = ['mp3','wav','aac','flac','m4a','opus'];
    let snippet = null;
    if (imageExts.includes(ext)) snippet = `![image](${text})`;
    else if (videoExts.includes(ext)) snippet = `<video controls src="${text}"></video>`;
    else if (audioExts.includes(ext)) snippet = `<audio controls src="${text}"></audio>`;

    if (snippet) {
      e.preventDefault();
      insertAtCursor(snippet);
    }
  } catch (_) {}
});

// ══════════════════════════════════════════════════
//  NEW FILE MODAL
// ══════════════════════════════════════════════════
function openNewModal() {
  document.getElementById('newFilename').value = '';
  document.getElementById('newError').style.display = 'none';
  document.getElementById('newFileModal').classList.add('show');
  setTimeout(() => document.getElementById('newFilename').focus(), 50);
}
function closeNewModal() {
  document.getElementById('newFileModal').classList.remove('show');
}
async function confirmNew() {
  const name  = document.getElementById('newFilename').value.trim();
  const errEl = document.getElementById('newError');
  if (!name) { errEl.textContent = 'Enter a filename.'; errEl.style.display = 'block'; return; }

  const res  = await fetch('/api/new', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: name }),
  });
  const data = await res.json();
  if (!data.ok) { errEl.textContent = data.error; errEl.style.display = 'block'; return; }

  closeNewModal();
  await loadFiles();
  setMode('editor');
  await openFile(data.file, true);
}

// ══════════════════════════════════════════════════
//  BROWSER HISTORY
// ══════════════════════════════════════════════════
window.addEventListener('popstate', (e) => {
  const file = e.state?.file || getUrlFile();
  if (file && file !== current) openFile(file, false);
});

// ══════════════════════════════════════════════════
//  INIT
// ══════════════════════════════════════════════════
(async () => {
  const savedTheme = localStorage.getItem('md-theme') || 'dark';
  applyTheme(savedTheme);

  await loadFiles();

  const urlFile     = getUrlFile();
  const initialFile = __INITIAL_FILE__;
  const fileToOpen  = urlFile || initialFile || null;
  if (fileToOpen) await openFile(fileToOpen, !urlFile);
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
    print("Markdown Viewer / Editor")
    print("=" * 60)
    print(f"Markdown : {base_path}")
    print(f"Artifacts: {artifact_path}")
    print(f"URL      : http://{HOST}:{PORT}")
    print("=" * 60)
    app.run(host=HOST, port=PORT, debug=False)
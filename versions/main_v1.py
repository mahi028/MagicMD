from flask import Flask, request, jsonify, Response, redirect
import json
from pathlib import Path
import markdown
import html
import re

# ============================================================
# CONFIG
# ============================================================

MARKDOWN_FOLDER = r"/home/mohittewari/papers/papers/summary"  # CHANGE THIS
HOST = "0.0.0.0"
PORT = 8890

# ============================================================
# SETUP
# ============================================================

app = Flask(__name__)
base_path = Path(MARKDOWN_FOLDER).resolve()


def get_markdown_files():
    files = []
    for path in base_path.rglob("*.md"):
        if ".ipynb_checkpoints" in path.parts:
            continue
        rel = path.relative_to(base_path)
        files.append(str(rel).replace("\\", "/"))
    return sorted(files)


def resolve_safe_path(relative_path):
    # Strip any query string fragments just in case
    relative_path = relative_path.split("?")[0].split("#")[0]
    # Only allow .md files
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
    """Return relative path to README.md if it exists in the root."""
    readme = base_path / "README.md"
    if readme.exists():
        return "README.md"
    # Case-insensitive check
    for f in base_path.iterdir():
        if f.name.lower() == "readme.md":
            return f.name
    return None


def render_markdown(md_text):
    # ── Step 1: stash math blocks so markdown doesn't mangle them ──
    math_stash = {}
    counter = [0]

    def stash(expr):
        key = f"MATHSTASH{counter[0]}END"
        math_stash[key] = expr
        counter[0] += 1
        return key

    # Display math: $$...$$ (must come before inline)
    md_text = re.sub(
        r"\$\$(.+?)\$\$",
        lambda m: stash(f'<span class="math-display">\\({m.group(1).strip()}\\)</span>'),
        md_text,
        flags=re.DOTALL,
    )
    # Inline math: $...$
    md_text = re.sub(
        r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)",
        lambda m: stash(f'<span class="math-inline">\\({m.group(1).strip()}\\)</span>'),
        md_text,
    )

    # ── Step 2: mermaid blocks ──
    def mermaid_replacer(match):
        code = match.group(1).strip()
        return f'\n<div class="mermaid">\n{html.escape(code)}\n</div>\n'

    md_text = re.sub(
        r"```mermaid\s*\n(.*?)```",
        mermaid_replacer,
        md_text,
        flags=re.DOTALL,
    )

    # ── Step 3: render markdown ──
    result = markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "toc", "nl2br"],
    )

    # ── Step 4: restore stashed math ──
    for key, val in math_stash.items():
        result = result.replace(f"<p>{key}</p>", val)
        result = result.replace(key, val)

    return result


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def index():
    # If ?file= param is present, validate it's a .md file
    file_param = request.args.get("file", "").strip()
    if file_param and not file_param.lower().endswith(".md"):
        return Response("Only .md files are allowed.", status=400, mimetype="text/plain")

    # Auto-open README if no file specified
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
    """Return a tree: root-level files + folders with their files."""
    flat = get_markdown_files()
    root_files = []
    folder_map = {}   # folder_name -> [full_relative_path, ...]

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
<link id="bsLight" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" disabled>
<link id="bsDark"  rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css">
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
/* ══════════════════════════════════════════
   DESIGN TOKENS — dark (default)
══════════════════════════════════════════ */
:root {
  --bg-base:       #0b1120;
  --bg-panel:      #111827;
  --bg-panel-2:    #1c2a3d;
  --bg-article:    #111827;
  --border:        #1f3148;
  --text-primary:  #e2e8f0;
  --text-muted:    #7e9ab5;
  --text-body:     #cbd5e1;
  --accent:        #3b82f6;
  --accent-hover:  #60a5fa;
  --sidebar-w:     300px;
  --topbar-h:      52px;
  --code-bg:       #0b1220;
  --pre-shadow:    0 8px 32px rgba(0,0,0,.35);
  --scrollbar:     #1f3a56;

  /* hl.js re-highlight trigger */
  --theme-id: dark;
}

/* ══════════════════════════════════════════
   DESIGN TOKENS — light
══════════════════════════════════════════ */
[data-theme="light"] {
  --bg-base:       #f0f4f8;
  --bg-panel:      #ffffff;
  --bg-panel-2:    #e8edf5;
  --bg-article:    #ffffff;
  --border:        #d1dce8;
  --text-primary:  #0f172a;
  --text-muted:    #64748b;
  --text-body:     #334155;
  --accent:        #2563eb;
  --accent-hover:  #1d4ed8;
  --code-bg:       #f1f5f9;
  --pre-shadow:    0 4px 18px rgba(0,0,0,.10);
  --scrollbar:     #b0c1d4;

  --theme-id: light;
}

/* ── Reset & base ─────────────────────────── */
*, *::before, *::after { box-sizing: border-box; }

html, body {
  height: 100%;
  overflow: hidden;
  background: var(--bg-base);
  color: var(--text-primary);
  font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  transition: background .2s, color .2s;
}

/* ── App shell ─────────────────────────────── */
#app     { display: flex; height: 100vh; }
#sidebar {
  width: var(--sidebar-w); min-width: var(--sidebar-w);
  background: var(--bg-panel);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  transition: transform .25s ease, background .2s, border-color .2s;
  z-index: 10;
}
#main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

/* Mobile sidebar overlay */
@media (max-width: 767.98px) {
  #sidebar {
    position: fixed; top: 0; left: 0; bottom: 0;
    transform: translateX(-100%);
  }
  #sidebar.show { transform: translateX(0); box-shadow: 6px 0 30px rgba(0,0,0,.4); }
  #sidebarBackdrop {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,.45); z-index: 9;
  }
  #sidebarBackdrop.show { display: block; }
}

/* ── Sidebar internals ──────────────────────── */
.sidebar-head {
  padding: 14px 12px;
  border-bottom: 1px solid var(--border);
  transition: border-color .2s;
}
.sidebar-brand {
  display: flex; align-items: center; gap: 8px;
  font-size: 14px; font-weight: 700;
  color: var(--text-primary); margin-bottom: 10px;
}
.sidebar-brand svg { flex-shrink: 0; }

#search {
  width: 100%;
  background: var(--bg-panel-2);
  color: var(--text-primary);
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 6px 10px;
  font-size: 13px;
  outline: none;
  transition: border-color .15s, background .2s;
}
#search:focus { border-color: var(--accent); }
#search::placeholder { color: var(--text-muted); }

.sidebar-actions { display: flex; gap: 7px; margin-top: 8px; }

.btn-side {
  flex: 1; padding: 5px 10px; font-size: 12px; font-weight: 600;
  border-radius: 7px; cursor: pointer; border: 1px solid var(--border);
  background: var(--bg-panel-2); color: var(--text-primary);
  transition: background .15s, border-color .15s, color .15s;
  display: flex; align-items: center; justify-content: center; gap: 5px;
}
.btn-side:hover { background: var(--bg-panel); border-color: var(--accent); color: var(--accent); }
.btn-side.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-side.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); color: #fff; }

#fileList { flex: 1; overflow-y: auto; padding: 4px 0; }

.folder-row {
  padding: 8px 14px;
  cursor: pointer;
  font-size: 12px; font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase; letter-spacing: .08em;
  display: flex; align-items: center; gap: 6px;
  border-bottom: 1px solid rgba(128,128,128,.06);
  user-select: none;
  transition: background .12s, color .12s;
}
.folder-row:hover { background: var(--bg-panel-2); color: var(--text-primary); }
.folder-row .folder-chevron {
  font-size: 10px; transition: transform .18s; flex-shrink: 0;
}
.folder-row.open .folder-chevron { transform: rotate(90deg); }
.folder-row .folder-count {
  margin-left: auto; font-size: 10px; font-weight: 400;
  color: var(--text-muted); opacity: .6;
}

.folder-children { display: none; }
.folder-children.open { display: block; }

.file-item {
  padding: 8px 14px 8px 30px;
  border-bottom: 1px solid rgba(128,128,128,.05);
  cursor: pointer;
  font-size: 12.5px;
  color: var(--text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  transition: background .12s, color .12s, border-left .12s;
  border-left: 3px solid transparent;
  letter-spacing: .01em;
}
.file-item.root { padding-left: 14px; }
.file-item:hover  { background: var(--bg-panel-2); color: var(--text-primary); }
.file-item.active {
  background: var(--bg-panel-2);
  border-left-color: var(--accent);
  color: var(--text-primary);
  font-weight: 600;
}
.file-item.readme { color: var(--accent); }
.file-item.readme.active { color: var(--accent); }

/* search result flat view */
.file-item .file-folder-tag {
  font-size: 10px; color: var(--text-muted);
  margin-right: 5px; opacity: .7;
}

/* ── Topbar ──────────────────────────────────── */
#topbar {
  height: var(--topbar-h);
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 8px;
  padding: 0 14px; flex-shrink: 0;
  transition: background .2s, border-color .2s;
}

#topbarTitle {
  flex: 1; font-size: 12.5px; color: var(--text-muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0;
}
#topbarTitle .paper-name {
  color: var(--text-primary); font-weight: 600; font-size: 13px;
}

#saveStatus { font-size: 11.5px; white-space: nowrap; font-weight: 600; }
#saveStatus.saved   { color: #22c55e; }
#saveStatus.unsaved { color: #f59e0b; }
#saveStatus.error   { color: #ef4444; }

/* Theme toggle */
#themeToggle {
  width: 34px; height: 34px;
  display: flex; align-items: center; justify-content: center;
  border-radius: 8px; border: 1px solid var(--border);
  background: var(--bg-panel-2); color: var(--text-muted);
  cursor: pointer; font-size: 16px;
  transition: background .15s, color .15s, border-color .15s;
  flex-shrink: 0;
}
#themeToggle:hover { background: var(--bg-panel); color: var(--accent); border-color: var(--accent); }

/* Mode tabs */
.tab-group {
  display: flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; flex-shrink: 0;
}
.tab-btn {
  padding: 5px 12px; font-size: 12px; font-weight: 600; cursor: pointer;
  background: var(--bg-panel-2); color: var(--text-muted);
  border: none; outline: none;
  transition: background .12s, color .12s;
}
.tab-btn:first-child { border-right: 1px solid var(--border); }
.tab-btn.active { background: var(--accent); color: #fff; }
.tab-btn:hover:not(.active) { background: var(--bg-panel); color: var(--text-primary); }

.btn-save {
  padding: 5px 12px; font-size: 12px; font-weight: 600;
  background: #16a34a; color: #fff; border: none; border-radius: 8px;
  cursor: pointer; display: none; gap: 5px; align-items: center;
  transition: background .15s;
}
.btn-save:hover { background: #15803d; }

/* ── Workspace ───────────────────────────────── */
#workspace { flex: 1; display: flex; overflow: hidden; }

/* ── Preview ─────────────────────────────────── */
#previewWrap { flex: 1; overflow-y: auto; padding: 2rem; background: var(--bg-base); transition: background .2s; }

.article {
  max-width: 860px; margin: 0 auto;
  background: var(--bg-article);
  border-radius: 16px;
  padding: 2.5rem 3rem;
  box-shadow: var(--pre-shadow);
  border: 1px solid var(--border);
  transition: background .2s, box-shadow .2s, border-color .2s;
}
@media (max-width: 575.98px) {
  #previewWrap { padding: .75rem; }
  .article { padding: 1.25rem 1rem; border-radius: 10px; }
}

/* ── Editor ──────────────────────────────────── */
#editorWrap { flex: 1; display: flex; overflow: hidden; }
#editorPane  { flex: 1; display: flex; flex-direction: column; border-right: 1px solid var(--border); overflow: hidden; transition: border-color .2s; }
#previewPane { flex: 1; overflow-y: auto; padding: 1rem 1.5rem; background: var(--bg-base); transition: background .2s; }
#previewPane .article { max-width: 100%; padding: 1.5rem 2rem; }

@media (max-width: 767.98px) {
  #editorWrap  { flex-direction: column; }
  #editorPane  { flex: 1; border-right: none; border-bottom: 1px solid var(--border); }
  #previewPane { flex: 1; }
  #previewPane .article { padding: 1rem; }
}

.pane-label {
  padding: 6px 14px; font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 1.2px;
  color: var(--text-muted);
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border); flex-shrink: 0;
  transition: background .2s, border-color .2s;
}

#editor {
  flex: 1; resize: none;
  background: var(--code-bg); color: var(--text-primary);
  font-family: 'JetBrains Mono', Consolas, 'Fira Code', monospace;
  font-size: 13.5px; line-height: 1.75;
  padding: 1rem 1.25rem;
  border: none; outline: none; tab-size: 4;
  transition: background .2s, color .2s;
}

/* ── Markdown prose ──────────────────────────── */
.article h1, .article h2, .article h3, .article h4 { color: var(--text-primary); font-weight: 700; }
.article h1 { font-size: 1.9rem; margin-bottom: .4em; line-height: 1.25; }
.article h2 { font-size: 1.4rem; margin: 1.4em 0 .35em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
.article h3 { font-size: 1.1rem; margin: 1.1em 0 .3em; }
.article p, .article li { line-height: 1.85; color: var(--text-body); margin-bottom: .55em; }
.article code {
  font-family: 'JetBrains Mono', Consolas, monospace; font-size: .85em;
  background: var(--bg-panel-2); color: var(--accent-hover);
  padding: .15em .4em; border-radius: 4px;
  border: 1px solid var(--border);
}
.article pre  {
  border-radius: 10px; overflow: auto; padding: 14px;
  background: var(--code-bg) !important; margin: 1em 0;
  border: 1px solid var(--border);
}
.article pre code { background: transparent; border: none; padding: 0; color: inherit; }
.article blockquote {
  border-left: 3px solid var(--accent); padding: .5em 1em;
  color: var(--text-muted); margin: 1em 0;
  background: var(--bg-panel-2); border-radius: 0 8px 8px 0;
}
.article table { width: 100%; border-collapse: collapse; margin: 1em 0; font-size: .93em; }
.article th, .article td { border: 1px solid var(--border); padding: 9px 12px; }
.article th { background: var(--bg-panel-2); color: var(--text-primary); font-weight: 600; }
.article tr:nth-child(even) td { background: rgba(128,128,128,.04); }
.article a { color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }
.article a:hover { color: var(--accent-hover); }
.article img { max-width: 100%; border-radius: 10px; display: block; margin: 1em auto; }
.article hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }

.article-footer {
  margin-top: 2.5rem; padding-top: 1rem;
  border-top: 1px solid var(--border);
  font-size: 11px; color: var(--text-muted);
  display: flex; justify-content: space-between; align-items: center;
}

.math-display { display: block; text-align: center; margin: 1.4em 0; overflow-x: auto; }
/* mermaid container — SVG is injected inside; background must match the mermaid theme */
.mermaid {
  border-radius: 12px; padding: 16px; overflow: auto; margin: 1.2em 0;
  background: #1e1e2e;         /* dark default — overridden by [data-theme=light] below */
  border: 1px solid var(--border);
}
[data-theme="light"] .mermaid { background: #f8fafc; }
.empty { text-align: center; color: var(--text-muted); padding: 5rem 0; font-size: 15px; }
.empty i { font-size: 3rem; display: block; margin-bottom: 12px; opacity: .4; }

/* ── Scrollbar ───────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-thumb { background: var(--scrollbar); border-radius: 999px; }
::-webkit-scrollbar-track { background: transparent; }

/* ── Modal ───────────────────────────────────── */
.modal-custom {
  position: fixed; inset: 0; z-index: 1000;
  display: none; align-items: center; justify-content: center;
  background: rgba(0,0,0,.5);
}
.modal-custom.show { display: flex; }
.modal-box {
  background: var(--bg-panel); border: 1px solid var(--border);
  border-radius: 14px; padding: 24px; width: 420px; max-width: 90vw;
  box-shadow: 0 20px 60px rgba(0,0,0,.4);
}
.modal-box h5 { margin: 0 0 16px; font-size: 15px; color: var(--text-primary); }
.modal-input {
  width: 100%; background: var(--bg-panel-2); color: var(--text-primary);
  border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 12px; font-size: 13px; outline: none;
  transition: border-color .15s;
}
.modal-input:focus { border-color: var(--accent); }
.modal-hint { font-size: 11.5px; color: var(--text-muted); margin-top: 6px; }
.modal-error { font-size: 12px; color: #ef4444; margin-top: 6px; display: none; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
.btn-modal {
  padding: 7px 16px; font-size: 13px; font-weight: 600; border-radius: 8px;
  border: 1px solid var(--border); cursor: pointer;
  background: var(--bg-panel-2); color: var(--text-primary);
  transition: background .12s;
}
.btn-modal:hover { background: var(--bg-panel); }
.btn-modal.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-modal.primary:hover { background: var(--accent-hover); border-color: var(--accent-hover); }
</style>
</head>
<body>
<div id="app">

  <!-- Sidebar backdrop (mobile) -->
  <div id="sidebarBackdrop" onclick="closeSidebar()"></div>

  <!-- ── Sidebar ── -->
  <aside id="sidebar">
    <div class="sidebar-head">
      <div class="sidebar-brand">
        <svg width="22" height="22" viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
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
        <button class="btn-side" onclick="loadFiles()">
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
      <button class="btn-side d-md-none" style="width:34px;height:34px;flex:none;padding:0"
        onclick="openSidebar()"><i class="bi bi-list" style="font-size:18px"></i></button>

      <span id="topbarTitle"><span style="color:var(--text-muted);font-size:12.5px">No file selected</span></span>

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
      <span id="saveStatus" class="small"></span>

      <button id="themeToggle" onclick="toggleTheme()" title="Toggle light/dark">
        <i class="bi bi-sun-fill" id="themeIcon"></i>
      </button>
    </div>

    <!-- Workspace -->
    <div id="workspace">

      <!-- Preview mode -->
      <div id="previewWrap">
        <article id="article" class="article">
          <div class="empty"><i class="bi bi-file-earmark-text"></i>Select a file from the sidebar</div>
        </article>
      </div>

      <!-- Editor mode (hidden) -->
      <div id="editorWrap" style="display:none">
        <div id="editorPane">
          <div class="pane-label">Markdown &nbsp;<kbd style="font-size:9px;background:var(--bg-panel-2);border:1px solid var(--border);border-radius:4px;padding:1px 5px;color:var(--text-muted)">Ctrl+S</kbd></div>
          <textarea id="editor" spellcheck="false"
            oninput="onEditorInput()" onkeydown="editorKeydown(event)"></textarea>
        </div>
        <div id="previewPane">
          <div class="pane-label">Preview</div>
          <article id="editorPreview" class="article"></article>
        </div>
      </div>

    </div>
  </div>
</div>

<!-- ── New File Modal ── -->
<div class="modal-custom" id="newFileModal" onclick="if(event.target===this)closeNewModal()">
  <div class="modal-box">
    <h5><i class="bi bi-file-earmark-plus me-2"></i>New file</h5>
    <input class="modal-input" id="newFilename"
      placeholder="e.g. notes/my-paper.md"
      onkeydown="if(event.key==='Enter')confirmNew(); if(event.key==='Escape')closeNewModal()">
    <div class="modal-hint">Path relative to library root. <code>.md</code> is added automatically.</div>
    <div class="modal-error" id="newError"></div>
    <div class="modal-footer">
      <button class="btn-modal" onclick="closeNewModal()">Cancel</button>
      <button class="btn-modal primary" onclick="confirmNew()">Create</button>
    </div>
  </div>
</div>

<script type="module">
import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
// Theme will be set properly by applyTheme() before any diagrams are rendered.
// We intentionally do NOT call initialize() here — applyTheme handles it.
window.mermaid = mermaid;
window.mermaidReady = true;
</script>

<script>
// ── State ──────────────────────────────────────────────────
let treeData        = { root_files: [], folders: [] };
let openFolders     = new Set();   // folder names currently expanded
let current         = null;
let mode            = 'preview';
let isDirty         = false;
let previewDebounce = null;
let theme           = 'dark';

// ── Theme ──────────────────────────────────────────────────
function mermaidTheme(t) { return t === 'dark' ? 'dark' : 'default'; }

function applyTheme(t) {
  theme = t;
  document.documentElement.setAttribute('data-theme', t);
  const icon = document.getElementById('themeIcon');
  icon.className = t === 'dark' ? 'bi bi-sun-fill' : 'bi bi-moon-fill';
  // Swap highlight.js stylesheet
  document.getElementById('hlDark' ).disabled = (t === 'light');
  document.getElementById('hlLight').disabled = (t === 'dark');
  // Re-apply hljs to any already-highlighted blocks
  document.querySelectorAll('pre code').forEach(b => {
    b.removeAttribute('data-highlighted');
    hljs.highlightElement(b);
  });
  // Re-render all mermaid diagrams with the new theme
  if (window.mermaid) {
    window.mermaid.initialize({ startOnLoad: false, theme: mermaidTheme(t), securityLevel: 'loose' });
    // Each .mermaid node stores its raw source in data-src set during renderInto().
    // We wipe the rendered SVG and re-run mermaid on the restored source.
    document.querySelectorAll('.mermaid[data-src]').forEach(async (el) => {
      el.removeAttribute('data-processed'); // mermaid v9 compat flag
      el.innerHTML = el.dataset.src;        // restore raw diagram text
      try { await window.mermaid.run({ nodes: [el] }); } catch(e) { console.warn('mermaid re-render:', e); }
    });
  }
  localStorage.setItem('md-theme', t);
}

function toggleTheme() {
  applyTheme(theme === 'dark' ? 'light' : 'dark');
}

// ── URL helpers ───────────────────────────────────────────

function getUrlFile() {
  const params = new URLSearchParams(window.location.search);
  return params.get('file') || '';
}

function setUrlFile(file) {
  const url = new URL(window.location.href);
  if (file) {
    url.searchParams.set('file', file);
  } else {
    url.searchParams.delete('file');
  }
  history.pushState({ file }, '', url.toString());
}

function slugTitle(file) {
  if (!file) return '';
  const name = file.split('/').pop().replace(/\.md$/i, '');
  return name.replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// ── Sidebar (mobile) ──────────────────────────────────────
function openSidebar()  {
  document.getElementById('sidebar').classList.add('show');
  document.getElementById('sidebarBackdrop').classList.add('show');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('show');
  document.getElementById('sidebarBackdrop').classList.remove('show');
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
  div.className = 'file-item'
    + (isRoot ? ' root' : '')
    + (file === current ? ' active' : '')
    + (isReadme ? ' readme' : '');
  div.title = file;
  if (isReadme) {
    div.innerHTML = `<i class="bi bi-bookmark-star-fill" style="font-size:11px;margin-right:5px"></i>${name}`;
  } else {
    div.textContent = name;
  }
  div.onclick = () => { openFile(file, true); closeSidebar(); };
  return div;
}

function renderTree() {
  const list = document.getElementById('fileList');
  list.innerHTML = '';

  // Root-level files first (README pinned at top)
  const rootSorted = [...treeData.root_files].sort((a, b) => {
    if (a.toLowerCase() === 'readme.md') return -1;
    if (b.toLowerCase() === 'readme.md') return 1;
    return a.localeCompare(b);
  });
  rootSorted.forEach(f => list.appendChild(makeFileItem(f, true)));

  // Folders
  treeData.folders.forEach(folder => {
    const isOpen = openFolders.has(folder.name);

    // Folder header row
    const row = document.createElement('div');
    row.className = 'folder-row' + (isOpen ? ' open' : '');
    row.innerHTML = `
      <i class="bi bi-chevron-right folder-chevron"></i>
      <i class="bi bi-folder${isOpen ? '2-open' : ''}-fill" style="font-size:12px;opacity:.7"></i>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${folder.name}</span>
      <span class="folder-count">${folder.files.length}</span>`;
    row.onclick = () => toggleFolder(folder.name);

    // Children container
    const children = document.createElement('div');
    children.className = 'folder-children' + (isOpen ? ' open' : '');
    folder.files.forEach(f => children.appendChild(makeFileItem(f, false)));

    list.appendChild(row);
    list.appendChild(children);
  });
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
  const list = document.getElementById('fileList');

  if (!q) { renderTree(); return; }

  // Flatten all files, match on full path (includes folder name)
  const allFlat = [
    ...treeData.root_files,
    ...treeData.folders.flatMap(f => f.files),
  ];
  const matched = allFlat.filter(f => f.toLowerCase().includes(q));

  list.innerHTML = '';
  matched.forEach(file => {
    const div = document.createElement('div');
    const isReadme = file.toLowerCase() === 'readme.md';
    const parts = file.split('/');
    const name = parts.pop().replace(/\.md$/i, '');
    const folder = parts.join('/');

    div.className = 'file-item root'
      + (file === current ? ' active' : '')
      + (isReadme ? ' readme' : '');
    div.title = file;

    // Show folder prefix as a small tag so context is clear
    if (folder) {
      div.innerHTML = `<span class="file-folder-tag">${folder}/</span>${name}`;
    } else if (isReadme) {
      div.innerHTML = `<i class="bi bi-bookmark-star-fill" style="font-size:11px;margin-right:5px"></i>${name}`;
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

// ── Open file ─────────────────────────────────────────────
async function openFile(file, updateUrl) {
  if (!file) return;
  if (isDirty && current) {
    if (!confirm(`Discard unsaved changes in "${current}"?`)) return;
  }
  current = file;
  isDirty = false;
  updateSaveStatus('');

  if (updateUrl) setUrlFile(file);

  // Auto-expand the file's parent folder
  const parts = file.split('/');
  if (parts.length > 1) openFolders.add(parts[0]);

  renderTree();

  // Topbar: show paper name
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
}

// ── Render ────────────────────────────────────────────────
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
  footer.innerHTML = `<span style="font-family:monospace">${current || ''}</span>
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
      // Stash raw source BEFORE mermaid replaces innerHTML with an SVG,
      // so applyTheme() can restore it and re-render on theme toggle.
      nodes.forEach(n => { n.dataset.src = n.innerHTML; });
      // Initialize with the current theme each time so freshly-loaded
      // diagrams always match the active theme.
      window.mermaid.initialize({ startOnLoad: false, theme: mermaidTheme(theme), securityLevel: 'loose' });
      try { await window.mermaid.run({ nodes }); } catch(e) { console.warn('mermaid render:', e); }
    }
  }
}

// ── Mode switching ────────────────────────────────────────
function setMode(m) {
  mode = m;
  document.getElementById('previewWrap').style.display = m === 'preview' ? 'flex' : 'none';
  document.getElementById('editorWrap' ).style.display = m === 'editor'  ? 'flex' : 'none';
  document.getElementById('saveBtn'    ).style.display = m === 'editor'  ? 'flex' : 'none';
  document.getElementById('tabPreview').classList.toggle('active', m === 'preview');
  document.getElementById('tabEditor' ).classList.toggle('active', m === 'editor');
}

// ── Editor ───────────────────────────────────────────────
function onEditorInput() {
  if (!isDirty) {
    isDirty = true;
    updateSaveStatus('unsaved');
    document.getElementById('saveBtn').style.display = 'flex';
  }
  clearTimeout(previewDebounce);
  previewDebounce = setTimeout(() => {
    renderInto('editorPreview', document.getElementById('editor').value);
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

// ── Save ─────────────────────────────────────────────────
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

// ── New file modal ────────────────────────────────────────
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
  document.getElementById('tabEditor').classList.add('active');
  document.getElementById('tabPreview').classList.remove('active');
  await openFile(data.file, true);
}

// ── Browser back/forward ──────────────────────────────────
window.addEventListener('popstate', (e) => {
  const file = e.state?.file || getUrlFile();
  if (file && file !== current) openFile(file, false);
});

// ── Init ─────────────────────────────────────────────────
(async () => {
  // Theme from localStorage, default dark
  const savedTheme = localStorage.getItem('md-theme') || 'dark';
  applyTheme(savedTheme);

  await loadFiles();

  // Priority: ?file= URL param > README.md > nothing
  const urlFile = getUrlFile();
  const initialFile = __INITIAL_FILE__;

  const fileToOpen = urlFile || initialFile || null;
  if (fileToOpen) {
    await openFile(fileToOpen, !urlFile);  // only push URL if not already in URL
  }
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
    print(f"Folder : {base_path}")
    print(f"URL    : http://{HOST}:{PORT}")
    print("=" * 60)

    app.run(host=HOST, port=PORT, debug=False)
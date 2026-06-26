# ````python
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from pathlib import Path
import urllib.parse
import markdown
import html
import re

# ============================================================
# CONFIG
# ============================================================

MARKDOWN_FOLDER = r"/home/mohittewari/engshare/athena/papers/summary"  # CHANGE THIS
HOST = "0.0.0.0"
PORT = 8890

# ============================================================
# SETUP
# ============================================================

base_path = Path(MARKDOWN_FOLDER).resolve()


def get_markdown_files():
    files = []

    for path in base_path.rglob("*.md"):

        # Skip Jupyter checkpoint folders
        if ".ipynb_checkpoints" in path.parts:
            continue

        rel = path.relative_to(base_path)
        files.append(str(rel).replace("\\", "/"))

    return sorted(files)

def read_markdown(relative_path):
    safe_path = (base_path / relative_path).resolve()

    # prevent path traversal
    if not str(safe_path).startswith(str(base_path)):
        return None

    if not safe_path.exists():
        return None

    return safe_path.read_text(encoding="utf-8")


def render_markdown(md_text):
    """
    Render markdown with Mermaid support.
    Mermaid blocks are intercepted before markdown parsing.
    All other code blocks remain normal highlighted code.
    """

    def mermaid_replacer(match):
        code = match.group(1).strip()

        return f"""
<div class="mermaid">
{html.escape(code)}
</div>
"""

    # Replace ONLY mermaid fenced blocks
    md_text = re.sub(
        r"```mermaid\s*\n(.*?)```",
        mermaid_replacer,
        md_text,
        flags=re.DOTALL,
    )

    return markdown.markdown(
        md_text,
        extensions=[
            "fenced_code",
            "tables",
            "toc",
            "codehilite",
            "nl2br",
        ],
    )


# ============================================================
# FRONTEND
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html lang="en">

<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">

<title>Markdown Viewer</title>

<link rel="stylesheet"
href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">

<script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>

<style>

:root {
    --bg: #0f172a;
    --panel: #111827;
    --panel-2: #1e293b;
    --border: #243244;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --blue: #3b82f6;
}

* {
    box-sizing: border-box;
}

html, body {
    margin: 0;
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family:
        Inter,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        sans-serif;
}

body {
    overflow: hidden;
}

.container {
    display: flex;
    height: 100vh;
}

/* =====================
   SIDEBAR
===================== */

.sidebar {
    width: 320px;
    min-width: 320px;
    background: var(--panel);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
}

.header {
    padding: 20px;
    border-bottom: 1px solid var(--border);
}

.title {
    font-size: 20px;
    font-weight: 700;
}

.subtitle {
    margin-top: 6px;
    color: var(--muted);
    font-size: 14px;
}

.controls {
    margin-top: 16px;
    display: flex;
    gap: 10px;
}

button {
    border: none;
    background: var(--blue);
    color: white;
    border-radius: 12px;
    padding: 10px 16px;
    cursor: pointer;
    font-weight: 600;
}

button:hover {
    opacity: 0.9;
}

.search {
    width: 100%;
    margin-top: 16px;
    background: #0b1220;
    border: 1px solid var(--border);
    color: white;
    border-radius: 12px;
    padding: 12px;
    outline: none;
}

.file-list {
    overflow-y: auto;
    flex: 1;
}

.file-item {
    padding: 14px 20px;
    border-bottom: 1px solid rgba(255,255,255,.03);
    cursor: pointer;
    transition: background .2s ease;
    color: #cbd5e1;
}

.file-item:hover {
    background: rgba(255,255,255,.05);
}

.file-item.active {
    background: var(--panel-2);
    border-left: 4px solid var(--blue);
}

/* =====================
   CONTENT
===================== */

.content {
    flex: 1;
    overflow-y: auto;
    padding: 40px;
}

.article {
    max-width: 1000px;
    margin: auto;
    background: var(--panel);
    border-radius: 22px;
    padding: 60px;
    box-shadow:
        0 10px 30px rgba(0,0,0,.15),
        0 40px 80px rgba(0,0,0,.2);
}

.empty {
    text-align: center;
    color: var(--muted);
    padding: 100px 0;
}

/* =====================
   MARKDOWN STYLING
===================== */

.article h1,
.article h2,
.article h3,
.article h4 {
    color: white;
}

.article h1 {
    font-size: 2.5rem;
}

.article p,
.article li {
    line-height: 1.8;
}

.article code {
    font-family: Consolas, monospace;
}

.article pre {
    border-radius: 14px;
    overflow: auto;
    padding: 16px;
    background: #0b1220 !important;
}

.article blockquote {
    border-left: 4px solid var(--blue);
    margin-left: 0;
    padding-left: 20px;
    color: var(--muted);
}

.article table {
    width: 100%;
    border-collapse: collapse;
}

.article th,
.article td {
    border: 1px solid var(--border);
    padding: 12px;
}

.article th {
    background: var(--panel-2);
}

.article a {
    color: #60a5fa;
}

.article img {
    max-width: 100%;
    border-radius: 14px;
}

/* Mermaid */

.mermaid {
    background: white;
    border-radius: 16px;
    padding: 20px;
    overflow: auto;
    margin: 24px 0;
}

/* Scrollbar */

::-webkit-scrollbar {
    width: 10px;
}

::-webkit-scrollbar-thumb {
    background: #2f4159;
    border-radius: 999px;
}

</style>
</head>

<body>

<div class="container">

    <aside class="sidebar">

        <div class="header">

            <div class="title">
                Markdown Library
            </div>

            <div class="subtitle">
                Auto-discovered .md files
            </div>

            <input
                id="search"
                class="search"
                placeholder="Search files..."
                oninput="filterFiles()"
            />

            <div class="controls">
                <button onclick="loadFiles()">
                    Refresh
                </button>
            </div>

        </div>

        <div id="fileList" class="file-list">
        </div>

    </aside>

    <main class="content">
        <article id="article" class="article">
            <div class="empty">
                Select a markdown file
            </div>
        </article>
    </main>

</div>

<script type="module">
import mermaid from
'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';

mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'loose'
});

window.mermaid = mermaid;
</script>

<script>

let allFiles = [];
let current = null;

async function loadFiles() {
    const res = await fetch('/api/files');
    allFiles = await res.json();

    renderFileList(allFiles);
}

function renderFileList(files) {

    const list = document.getElementById('fileList');
    list.innerHTML = '';

    files.forEach(file => {

        const div = document.createElement('div');

        div.className = 'file-item';
        div.textContent = file;

        if (file === current) {
            div.classList.add('active');
        }

        div.onclick = async () => {

            current = file;

            document.querySelectorAll('.file-item')
                .forEach(x => x.classList.remove('active'));

            div.classList.add('active');

            await loadMarkdown(file);
        };

        list.appendChild(div);
    });
}

function filterFiles() {

    const query =
        document.getElementById('search')
            .value
            .toLowerCase();

    const filtered = allFiles.filter(f =>
        f.toLowerCase().includes(query)
    );

    renderFileList(filtered);
}

async function loadMarkdown(file) {

    const res = await fetch(
        '/api/read?file=' +
        encodeURIComponent(file)
    );

    const html = await res.text();

    const article =
        document.getElementById('article');

    article.innerHTML = html;

    // Syntax highlight
    document.querySelectorAll('pre code')
        .forEach(el => hljs.highlightElement(el));

    // Mermaid render
    if (window.mermaid) {

        const mermaids =
            document.querySelectorAll('.mermaid');

        if (mermaids.length > 0) {

            try {
                await window.mermaid.run({
                    nodes: mermaids
                });
            } catch (err) {
                console.error(
                    'Mermaid render error:',
                    err
                );
            }
        }
    }
}

loadFiles();

</script>

</body>
</html>
"""


# ============================================================
# SERVER
# ============================================================

class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # Homepage
        if parsed.path == "/":
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8"
            )
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))
            return

        # List markdown files
        if parsed.path == "/api/files":
            files = get_markdown_files()

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json"
            )
            self.end_headers()
            self.wfile.write(
                json.dumps(files).encode()
            )
            return

        # Read markdown file
        if parsed.path == "/api/read":

            params = urllib.parse.parse_qs(
                parsed.query
            )

            file = params.get(
                "file",
                [""]
            )[0]

            md_text = read_markdown(file)

            if md_text is None:
                self.send_response(404)
                self.end_headers()
                return

            html_output = render_markdown(md_text)

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8"
            )
            self.end_headers()

            self.wfile.write(
                html_output.encode("utf-8")
            )
            return

        self.send_response(404)
        self.end_headers()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("Markdown Viewer")
    print("=" * 60)
    print(f"Folder : {base_path}")
    print(f"URL    : http://{HOST}:{PORT}")
    print("=" * 60)

    server = HTTPServer((HOST, PORT), Handler)
    server.serve_forever()


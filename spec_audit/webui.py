"""
spec-audit local web demo -- `spec-audit --serve`.

Not a SaaS, not a remote service: this starts a plain-stdlib HTTP server
bound to localhost only, serving a single page that lets a user drag a
folder into the browser. The browser reads the dropped files client-side
(standard File API, nothing leaves the machine) and POSTs their relative
paths + text content to this same local server, which writes them into a
temporary directory and runs the exact same, unmodified detection engine
(`spec_audit.cli.run`) used by the CLI. The HTML report returned is the
same `_render_html` already used by `--format html` -- this module adds
zero new detection logic, only a local transport for people who would
rather drag a folder than open a terminal.
"""

from __future__ import annotations

import http.server
import json
import shutil
import tempfile
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from spec_audit.cli import _render_html, run

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>spec-audit -- local demo</title>
<style>
  body { font-family: -apple-system, Segoe UI, sans-serif; background: #0d1117; color: #c9d1d9;
         padding: 2rem; max-width: 760px; margin: auto; }
  h1 { color: #58a6ff; }
  p.sub { color: #8b949e; }
  #dropzone { border: 2px dashed #30363d; border-radius: 10px; padding: 3rem 1.5rem; text-align: center;
              margin-top: 1.5rem; cursor: pointer; transition: border-color 0.15s; }
  #dropzone.drag { border-color: #58a6ff; background: #161b22; }
  #dropzone p { margin: 0.3rem 0; }
  #status { margin-top: 1rem; color: #8b949e; }
  #report { margin-top: 1.5rem; }
  .note { font-size: 0.85rem; color: #8b949e; margin-top: 0.75rem; }
  input[type=file] { display: none; }
</style>
</head>
<body>
<h1>spec-audit</h1>
<p class="sub">Local demo -- nothing leaves this machine. Drop a folder, see the report.</p>
<div id="dropzone">
  <p><strong>Drag a folder here</strong></p>
  <p>or click to choose one</p>
  <input type="file" id="filepicker" webkitdirectory directory multiple>
</div>
<p class="note">Only .py files are read. Everything runs on http://localhost -- no upload to any external server.</p>
<div id="status"></div>
<div id="report"></div>
<script>
const dropzone = document.getElementById('dropzone');
const picker = document.getElementById('filepicker');
const statusEl = document.getElementById('status');
const reportEl = document.getElementById('report');

dropzone.addEventListener('click', () => picker.click());
picker.addEventListener('change', (e) => handleFiles(e.target.files));

['dragenter', 'dragover'].forEach(evt =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add('drag'); }));
['dragleave', 'drop'].forEach(evt =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove('drag'); }));
dropzone.addEventListener('drop', (e) => {
  const items = e.dataTransfer.items;
  const files = [];
  function walk(entry, path) {
    return new Promise((resolve) => {
      if (entry.isFile) {
        entry.file((f) => { f.relPath = path + f.name; files.push(f); resolve(); });
      } else if (entry.isDirectory) {
        const reader = entry.createReader();
        reader.readEntries((entries) => {
          Promise.all(entries.map(en => walk(en, path + entry.name + '/'))).then(resolve);
        });
      } else { resolve(); }
    });
  }
  const walks = [];
  for (let i = 0; i < items.length; i++) {
    const entry = items[i].webkitGetAsEntry();
    if (entry) walks.push(walk(entry, ''));
  }
  Promise.all(walks).then(() => handleFiles(files));
});

async function handleFiles(fileList) {
  const pyFiles = Array.from(fileList).filter(f => f.name.endsWith('.py'));
  if (pyFiles.length === 0) {
    statusEl.textContent = 'No .py files found in the dropped folder.';
    return;
  }
  statusEl.textContent = `Reading ${pyFiles.length} file(s)...`;
  const payload = {};
  for (const f of pyFiles) {
    const text = await f.text();
    const rel = f.relPath || f.webkitRelativePath || f.name;
    payload[rel] = text;
  }
  statusEl.textContent = 'Analyzing...';
  const resp = await fetch('/analyze', { method: 'POST', body: JSON.stringify(payload) });
  const html = await resp.text();
  statusEl.textContent = `Done -- ${pyFiles.length} file(s) analyzed.`;
  reportEl.innerHTML = html;
}
</script>
</body>
</html>"""


def _relativize_paths(report_dict: dict, tmp_dir: str) -> None:
    """Cosmetic only: strip the server's temp-directory prefix from
    reported file paths so the browser shows the dropped folder's own
    relative layout instead of an absolute machine-specific path."""
    prefix = str(Path(tmp_dir)) + "\\" if "\\" in str(Path(tmp_dir)) else str(Path(tmp_dir)) + "/"
    prefix_alt = str(Path(tmp_dir).as_posix()) + "/"

    def strip(p: str) -> str:
        for pre in (prefix, prefix_alt):
            if p.startswith(pre):
                return p[len(pre):]
        return p

    for v in report_dict.get("violations", []):
        v["file"] = strip(v["file"])
        v["message"] = v["message"].replace(str(tmp_dir) + "\\", "").replace(str(tmp_dir) + "/", "")
        if v.get("heuristic_source"):
            v["heuristic_source"] = v["heuristic_source"].replace(str(tmp_dir) + "\\", "").replace(
                str(tmp_dir) + "/", ""
            )
        for c in v.get("conflict", []) or []:
            c["file"] = strip(c["file"])


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A002 -- quiet by default
        pass

    def do_GET(self):
        if urlparse(self.path).path != "/":
            self.send_response(404)
            self.end_headers()
            return
        body = _PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/analyze":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            files: dict[str, str] = json.loads(raw)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        tmp_dir = tempfile.mkdtemp(prefix="spec_audit_webui_")
        try:
            for rel_path, content in files.items():
                # Reject path components that would escape tmp_dir
                # (parent-dir traversal, absolute paths) -- the browser
                # never sends these for a real dropped folder, but the
                # server does not trust client input regardless.
                parts = Path(rel_path).parts
                if any(p in ("..", "") for p in parts) or Path(rel_path).is_absolute():
                    continue
                dest = Path(tmp_dir) / Path(*parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")

            report = run(Path(tmp_dir))
            report_dict = report.to_dict()
            _relativize_paths(report_dict, tmp_dir)
            html_fragment = _render_html(report_dict)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        body = html_fragment.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port: int = 8765, open_browser: bool = True) -> None:
    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"spec-audit local demo running at {url}  (Ctrl+C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

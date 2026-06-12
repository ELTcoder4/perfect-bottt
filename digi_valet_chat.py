#!/usr/bin/env python3
"""
digi_valet.py  —  Digi Valet: All-in-One
=========================================
Merges digi_valet.html  +  org_tree.py into a single Python file.

How it works
------------
1.  A lightweight HTTP server is started on http://localhost:5173
2.  GET  /          → serves the embedded HTML UI (browser-based chat)
3.  GET  /api/orgtree?q=<query>  → org-chart lookup (JSON response)
4.  The browser is opened automatically at startup.
5.  Press Ctrl-C in the terminal to stop.

Requirements
------------
- Python 3.8+  (no pip packages needed)
- Ollama running locally  (`ollama serve`)
- Optional: place OrgTree.csv next to this file for /digivalet lookups

Usage
-----
    python digi_valet.py
    python digi_valet.py --port 8080
    python digi_valet.py --csv /path/to/OrgTree.csv --no-browser
"""

import csv
import json
import sys
import argparse
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs, unquote_plus


# ══════════════════════════════════════════════════════════════════════════════
#  ORG TREE  (inlined from org_tree.py)
# ══════════════════════════════════════════════════════════════════════════════

class OrgTree:
    """
    Loads employee data from a CSV file and provides lookup / search helpers.

    Expected CSV columns (header row, exact names):
        Employee Number, Display Name, Job Title, Department, Location, Reportees Count
    """

    def __init__(self, csv_path: str = None):
        self.employees: dict = {}      # emp_num -> info dict
        self.name_to_num: dict = {}    # lowercase name -> emp_num
        self.loaded: bool = False
        self.source_path: Optional[str] = None
        if csv_path and Path(csv_path).exists():
            self.load_from_csv(csv_path)

    # ── Loading ──────────────────────────────────────────────────────────────

    _FIELD_ORDER = ['emp_num', 'name', 'title', 'dept', 'location', 'reports_count']

    def _parse_row(self, row: dict) -> Optional[dict]:
        """Return a normalised employee dict from a csv.DictReader row."""
        emp_num_raw = (row.get('Employee Number') or '').strip()
        if not emp_num_raw:
            return None

        # Layout 1: properly comma-separated
        name_raw = (row.get('Display Name') or '').strip()
        if name_raw:
            try:
                reports_count = int((row.get('Reportees Count') or '0').strip() or 0)
            except ValueError:
                reports_count = 0
            return {
                'emp_num':       emp_num_raw,
                'name':          name_raw,
                'title':         (row.get('Job Title') or '').strip(),
                'dept':          (row.get('Department') or '').strip(),
                'location':      (row.get('Location') or '').strip(),
                'reports_count': reports_count,
            }

        # Layout 2: all fields tab-joined inside 'Employee Number'
        if '\t' in emp_num_raw:
            parts = [p.strip() for p in emp_num_raw.split('\t')]
            if len(parts) >= 2:
                try:
                    reports_count = int(parts[5]) if len(parts) > 5 else 0
                except ValueError:
                    reports_count = 0
                return {
                    'emp_num':       parts[0],
                    'name':          parts[1] if len(parts) > 1 else '',
                    'title':         parts[2] if len(parts) > 2 else '',
                    'dept':          parts[3] if len(parts) > 3 else '',
                    'location':      parts[4] if len(parts) > 4 else '',
                    'reports_count': reports_count,
                }

        return None

    def load_from_csv(self, csv_path: str) -> str:
        self.employees.clear()
        self.name_to_num.clear()
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    info = self._parse_row(row)
                    if info is None:
                        continue
                    emp_num = info['emp_num']
                    if not emp_num:
                        continue
                    self.employees[emp_num] = info
                    if info['name']:
                        self.name_to_num[info['name'].lower()] = emp_num
            self.loaded = len(self.employees) > 0
            self.source_path = csv_path
            if self.loaded:
                return f"✅ Loaded {len(self.employees)} employees from {Path(csv_path).name}"
            return f"⚠️ No employee rows found in {Path(csv_path).name}"
        except Exception as e:
            self.loaded = False
            return f"❌ Failed to load org tree: {e}"

    # ── Lookups ──────────────────────────────────────────────────────────────

    def get_employee(self, identifier: str) -> Optional[dict]:
        ident = (identifier or '').strip()
        if not ident:
            return None
        if ident in self.employees:
            return self.employees[ident]
        if ident.lower() in self.name_to_num:
            return self.employees[self.name_to_num[ident.lower()]]
        return None

    SYNONYMS = {
        "qa": "quality assurance",
        "hr": "human resources",
        "it": "it & network support",
        "ceo": "ceo",
        "vp": "vp",
        "pm": "program manager",
    }

    def search(self, query: str, limit: int = 25) -> list:
        if not self.loaded:
            return []
        q = (query or '').strip().lower()
        if not q:
            return []
        q = self.SYNONYMS.get(q, q)
        results = []
        for info in self.employees.values():
            haystack = " ".join([
                info['name'], info['title'], info['dept'], info['location'],
            ]).lower()
            if q in haystack:
                results.append(info)
        return results[:limit]

    def list_by_department(self, dept_query: str, limit: int = 50) -> list:
        if not self.loaded:
            return []
        q = (dept_query or '').strip().lower()
        return [i for i in self.employees.values() if q in i['dept'].lower()][:limit]

    def list_by_location(self, loc_query: str, limit: int = 50) -> list:
        if not self.loaded:
            return []
        q = (loc_query or '').strip().lower()
        return [i for i in self.employees.values() if q in i['location'].lower()][:limit]

    def stats(self) -> dict:
        depts: dict = {}
        locs: dict = {}
        for info in self.employees.values():
            depts[info['dept']] = depts.get(info['dept'], 0) + 1
            locs[info['location']] = locs.get(info['location'], 0) + 1
        return {
            'total': len(self.employees),
            'departments': dict(sorted(depts.items(), key=lambda x: -x[1])),
            'locations': dict(sorted(locs.items(), key=lambda x: -x[1])),
        }

    # ── Formatting helpers ───────────────────────────────────────────────────

    @staticmethod
    def format_employee(info: dict) -> str:
        reports = info.get('reports_count', 0)
        reports_line = f"\n• Direct reports: {reports}" if reports else "\n• Direct reports: 0"
        return (
            f"**{info['name']}** ({info['emp_num']})\n"
            f"• Title: {info['title']}\n"
            f"• Department: {info['dept']}\n"
            f"• Location: {info['location']}"
            f"{reports_line}"
        )

    @staticmethod
    def format_results_table(results: list) -> str:
        if not results:
            return "No matches found."
        lines = ["| Emp # | Name | Title | Department | Location |",
                 "|---|---|---|---|---|"]
        for info in results:
            lines.append(
                f"| {info['emp_num']} | {info['name']} | {info['title']} | "
                f"{info['dept']} | {info['location']} |"
            )
        return "\n".join(lines)

    def handle_query(self, query: str) -> str:
        """Process a /digivalet query string and return a Markdown response."""
        if not self.loaded:
            return (
                "⚠️ The org tree hasn't been loaded yet.\n\n"
                "Place an **OrgTree.csv** file (columns: `Employee Number, Display Name, "
                "Job Title, Department, Location, Reportees Count`) next to "
                "`digi_valet.py`, or pass `--csv /path/to/OrgTree.csv` on startup."
            )

        q = (query or '').strip()
        if not q or q.lower() in ('help', '?', ''):
            stats = self.stats()
            dept_lines = "\n".join(
                f"  • {d}: {c}" for d, c in list(stats["departments"].items())[:10]
            )
            return (
                f"**Digi Valet Org Lookup** — {stats['total']} employees loaded\n\n"
                "Usage:\n"
                "• `/digivalet <name>` — e.g. `/digivalet Rahul Salgia`\n"
                "• `/digivalet <employee number>` — e.g. `/digivalet PB001`\n"
                "• `/digivalet <keyword>` — search title/dept/location (e.g. `/digivalet QA`)\n"
                "• `/digivalet stats` — overview of departments and locations\n\n"
                f"Top departments:\n{dept_lines}"
            )

        if q.lower() == 'stats':
            stats = self.stats()
            dept_lines = "\n".join(f"| {d} | {c} |" for d, c in stats["departments"].items())
            loc_lines  = "\n".join(f"| {l} | {c} |" for l, c in stats["locations"].items())
            return (
                f"**Org Tree Overview** — {stats['total']} employees\n\n"
                f"**By Department**\n\n| Department | Count |\n|---|---|\n{dept_lines}\n\n"
                f"**By Location**\n\n| Location | Count |\n|---|---|\n{loc_lines}"
            )

        emp = self.get_employee(q)
        if emp:
            return self.format_employee(emp)

        results = self.search(q)
        if not results:
            return f"No matches found in the org tree for **'{q}'**."
        return f"Found {len(results)} match(es) for **'{q}'**:\n\n" + self.format_results_table(results)


# ══════════════════════════════════════════════════════════════════════════════
#  HTML UI  (embedded from digi_valet.html with /digivalet integration added)
# ══════════════════════════════════════════════════════════════════════════════

def _build_html() -> str:
    """Return the full HTML string for the Digi Valet UI."""
    # Read the template (allow override by placing digi_valet.html next to this script)
    override = Path(__file__).resolve().parent / "digi_valet.html"
    if override.exists():
        html = override.read_text(encoding="utf-8")
    else:
        html = _EMBEDDED_HTML

    # ── Inject /digivalet chip ────────────────────────────────────────────────
    if '/digivalet' not in html:
        html = html.replace(
            "onclick=\"runCmd('/analyze')\">/analyze</button>\n  </div>",
            "onclick=\"runCmd('/analyze')\">/analyze</button>\n"
            "    <button class=\"cmd-chip\" onclick=\"runCmd('/digivalet')\">/digivalet</button>\n"
            "  </div>"
        )

    # ── Add /digivalet to CMDS dict ──────────────────────────────────────────
    if "'/digivalet'" not in html:
        html = html.replace(
            "'/analyze':  'I will share data with you.",
            "'/digivalet': null,  // handled by Python org-tree API\n"
            "  '/analyze':  'I will share data with you."
        )

    # ── Inject runCmd handler + fetch helper (before window.onload) ──────────
    if 'handleDigiValetLookup' not in html:
        orgtree_js = """
// ══════════════════════════════════════════════════════════════════════════════
//  ORG TREE  (talks to the Python /api/orgtree endpoint)
// ══════════════════════════════════════════════════════════════════════════════
async function handleDigiValetLookup(query) {
  appendBubble('user', '/digivalet ' + query);
  messages.push({ role: 'user', content: '/digivalet ' + query });
  allChats[currentChatId].messages = [...messages];
  if (!privacyMode) saveChats();
  renderHistoryList();
  showTyping(true);
  try {
    const resp = await fetch('/api/orgtree?q=' + encodeURIComponent(query));
    const data = await resp.json();
    hideTyping();
    const md = data.result || '⚠ No result returned.';
    appendBubble('assistant', md);
    messages.push({ role: 'assistant', content: md });
    allChats[currentChatId].messages = [...messages];
    if (!privacyMode) saveChats();
  } catch(e) {
    hideTyping();
    appendBubble('error',
      '⚠ Could not reach the org-tree API.\\n\\n' +
      'Make sure you started Digi Valet via `python digi_valet.py`.');
  }
  scrollBottom();
}

"""
        html = html.replace('window.onload = () => {', orgtree_js + 'window.onload = () => {')

    # ── Patch runCmd to intercept /digivalet ────────────────────────────────
    if 'handleDigiValetLookup' in html and "cmd === '/digivalet'" not in html:
        html = html.replace(
            "function runCmd(cmd) {\n  let prompt = CMDS[cmd] || '';",
            "function runCmd(cmd) {\n"
            "  if (cmd === '/digivalet') {\n"
            "    const q = window.prompt('Org lookup — enter name, employee ID, dept, location, or \"stats\":');\n"
            "    if (q === null) return;\n"
            "    handleDigiValetLookup(q.trim() || 'help');\n"
            "    return;\n"
            "  }\n"
            "  let prompt = CMDS[cmd] || '';"
        )

    return html


# ══════════════════════════════════════════════════════════════════════════════
#  HTTP SERVER
# ══════════════════════════════════════════════════════════════════════════════

# Module-level org tree instance (populated at startup)
_org_tree: OrgTree = OrgTree()
_html_cache: str = ""


class DigiValetHandler(BaseHTTPRequestHandler):
    """Serves the HTML UI and the /api/orgtree JSON endpoint."""

    def log_message(self, fmt, *args):
        # Only print non-asset requests to keep the terminal clean
        if not any(args[0].startswith(x) for x in ['GET / ', 'GET /api/']):
            return
        print(f"  {args[0]}")

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path in ('/', '/index.html'):
            self._serve_html()
        elif path == '/api/orgtree':
            self._serve_orgtree(parsed.query)
        else:
            self.send_error(404, "Not Found")

    def _serve_html(self):
        body = _html_cache.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_orgtree(self, query_string: str):
        qs = parse_qs(query_string)
        q  = unquote_plus(qs.get('q', [''])[0]).strip()
        result = _org_tree.handle_query(q)
        body = json.dumps({'result': result, 'query': q}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def _find_csv() -> Optional[str]:
    """Look for OrgTree.csv in common locations."""
    candidates = [
        Path(__file__).resolve().parent / "OrgTree.csv",
        Path(__file__).resolve().parent / "orgtree.csv",
        Path.home() / "OrgTree.csv",
        Path.home() / ".digi_valet_orgtree.csv",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def main():
    parser = argparse.ArgumentParser(description="Digi Valet — AI Personal Assistant")
    parser.add_argument('--port',       type=int, default=5173,
                        help="Port for the local web server (default: 5173)")
    parser.add_argument('--csv',        type=str, default=None,
                        help="Path to OrgTree.csv (auto-detected if not given)")
    parser.add_argument('--no-browser', action='store_true',
                        help="Don't open the browser automatically")
    args = parser.parse_args()

    global _org_tree, _html_cache

    # ── Load org tree ────────────────────────────────────────────────────────
    csv_path = args.csv or _find_csv()
    if csv_path:
        msg = _org_tree.load_from_csv(csv_path)
        print(msg)
    else:
        print("ℹ️  No OrgTree.csv found — /digivalet lookups will be unavailable.")
        print("   Place OrgTree.csv next to digi_valet.py to enable org-chart queries.\n")

    # ── Build HTML ───────────────────────────────────────────────────────────
    _html_cache = _build_html()

    # ── Start HTTP server ────────────────────────────────────────────────────
    server = HTTPServer(('127.0.0.1', args.port), DigiValetHandler)
    url = f"http://localhost:{args.port}"

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print(f"\n✨ Digi Valet is running at  {url}")
    print(f"   Press Ctrl-C to stop.\n")

    if not args.no_browser:
        # Small delay to let the server start before opening the browser
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Digi Valet stopped.")
        server.shutdown()


# ══════════════════════════════════════════════════════════════════════════════
#  EMBEDDED HTML  (fallback if digi_valet.html is not found next to the script)
#  Generated from digi_valet.html — do not edit below this line manually.
#  To customise the UI, place a digi_valet.html next to this file instead.
# ══════════════════════════════════════════════════════════════════════════════

_EMBEDDED_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Digi Valet</title>
<style>
  /* ── Reset & Base ──────────────────────────────────────────────────────── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:          #0e0c0a;
    --bg2:         #141210;
    --bg3:         #1a1612;
    --bg4:         #1e1a16;
    --border:      #2a2420;
    --border2:     #3a3028;
    --gold:        #c8a96e;
    --gold-light:  #dbbf82;
    --gold-dark:   #a88a50;
    --blue:        #8ab4c8;
    --text:        #d4cfc8;
    --text2:       #8a7a6a;
    --text3:       #5c5046;
    --text4:       #3a3028;
    --red:         #e07070;
    --green:       #6a9e6a;
    --radius:      10px;
    --radius-sm:   6px;
    --radius-pill: 20px;
    --font:        'Georgia', serif;
    --mono:        'Consolas', 'Monaco', monospace;
    --sidebar-w:   260px;
    --header-h:    52px;
    --cmd-h:       36px;
    --input-h:     auto;
    --chip-h:      36px;
  }

  body { font-family: var(--font); background: var(--bg); color: var(--text);
         height: 100vh; display: flex; overflow: hidden; }

  ::-webkit-scrollbar { width: 5px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 3px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--gold); }

  /* ── Sidebar ───────────────────────────────────────────────────────────── */
  #sidebar {
    width: var(--sidebar-w); min-width: var(--sidebar-w);
    background: var(--bg2); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; padding: 24px 16px 16px;
    gap: 0; overflow-y: auto; flex-shrink: 0;
  }

  .logo { color: var(--gold); font-size: 18px; letter-spacing: 3px; font-weight: bold; line-height: 1.2; }
  .tagline { color: var(--text3); font-size: 9px; letter-spacing: 3px; margin-top: 2px; }

  .section-label {
    color: var(--text4); font-size: 8px; letter-spacing: 2px;
    text-transform: uppercase; margin: 14px 0 5px;
  }

  select, .styled-select {
    width: 100%; background: var(--bg4); color: var(--gold);
    border: 1px solid var(--border2); border-radius: var(--radius-sm);
    padding: 6px 10px; font-family: var(--font); font-size: 10px;
    outline: none; cursor: pointer; appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23c8a96e'/%3E%3C/svg%3E");
    background-repeat: no-repeat; background-position: right 8px center;
  }
  select:focus { border-color: var(--gold); }

  .btn-sidebar {
    width: 100%; background: var(--bg4); color: var(--gold);
    border: 1px solid var(--border2); border-radius: var(--radius-sm);
    padding: 8px 12px; font-family: var(--font); font-size: 10px;
    cursor: pointer; text-align: left; transition: .15s;
    margin-top: 6px;
  }
  .btn-sidebar:hover { background: var(--border); border-color: var(--gold); }

  .btn-row { display: flex; gap: 6px; margin-top: 6px; }
  .btn-icon {
    flex: 1; background: var(--bg4); color: var(--text2);
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 5px 8px; font-family: var(--font); font-size: 9px;
    cursor: pointer; transition: .15s; text-align: center;
  }
  .btn-icon:hover { color: var(--gold); border-color: var(--gold); background: var(--border); }
  .btn-icon.active { color: var(--gold); border-color: var(--gold); background: var(--border2); }

  #history-list { list-style: none; display: flex; flex-direction: column; gap: 3px; margin-top: 6px; }
  #history-list li {
    padding: 7px 10px; border-radius: var(--radius-sm); cursor: pointer;
    font-size: 10px; color: var(--text2); white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; transition: .15s;
  }
  #history-list li:hover { background: var(--bg4); color: var(--gold); }
  #history-list li.active { background: var(--border); color: var(--gold); font-weight: bold; }

  #task-list { list-style: none; display: flex; flex-direction: column; gap: 3px;
               max-height: 120px; overflow-y: auto; margin-top: 4px; }
  #task-list li {
    display: flex; align-items: center; gap: 6px;
    font-size: 10px; padding: 5px 8px; border-radius: 5px;
    cursor: pointer; transition: .15s;
  }
  #task-list li:hover { background: var(--bg4); }
  #task-list li.done .task-text { text-decoration: line-through; color: var(--text4); }
  .task-dot { font-size: 12px; color: var(--gold); flex-shrink: 0; }
  .task-dot.done { color: var(--text4); }

  .task-input-row { display: flex; gap: 4px; margin-top: 6px; }
  .task-input-row input {
    flex: 1; background: var(--bg3); color: var(--gold);
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 5px 8px; font-family: var(--font); font-size: 10px; outline: none;
  }
  .task-input-row input:focus { border-color: var(--gold); }
  .task-input-row button {
    background: var(--bg4); color: var(--gold); border: 1px solid var(--border2);
    border-radius: var(--radius-sm); width: 26px; cursor: pointer;
    font-size: 16px; font-weight: bold; line-height: 1; transition: .15s;
  }
  .task-input-row button:hover { background: var(--border); border-color: var(--gold); }

  .status-dot {
    font-size: 9px; color: var(--text4); letter-spacing: 1px; margin-top: auto;
    padding-top: 10px; border-top: 1px solid var(--border);
  }
  .status-dot.connected { color: var(--green); }
  .status-dot.error { color: var(--red); }

  /* ── Main area ─────────────────────────────────────────────────────────── */
  #main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }

  #header {
    height: var(--header-h); background: var(--bg); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; padding: 0 20px; gap: 10px; flex-shrink: 0;
  }
  #conv-title { color: var(--text2); font-size: 12px; flex: 1; }
  .header-btn {
    background: var(--bg4); color: var(--text2); border: 1px solid var(--border);
    border-radius: var(--radius-sm); padding: 4px 8px; font-family: var(--font);
    font-size: 9px; cursor: pointer; transition: .15s;
  }
  .header-btn:hover { color: var(--gold); border-color: var(--gold); }
  #model-indicator { color: var(--text4); font-size: 9px; letter-spacing: 1px; }

  #cmd-bar {
    height: var(--cmd-h); background: var(--bg2); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; padding: 0 14px; gap: 5px;
    overflow-x: auto; flex-shrink: 0;
  }
  #cmd-bar::-webkit-scrollbar { height: 3px; }
  .cmd-chip {
    background: var(--bg4); color: var(--text2); border: 1px solid var(--border);
    border-radius: 4px; padding: 3px 8px; font-family: var(--font); font-size: 8px;
    cursor: pointer; white-space: nowrap; transition: .15s; flex-shrink: 0;
  }
  .cmd-chip:hover { color: var(--gold); border-color: var(--gold); }

  #chat-scroll {
    flex: 1; overflow-y: auto; padding: 20px 24px;
    display: flex; flex-direction: column; gap: 10px;
  }

  .bubble {
    display: flex; flex-direction: column; gap: 6px;
    padding: 12px 14px; border-radius: var(--radius);
    line-height: 1.65; font-size: 13px; position: relative;
    animation: bubbleIn .2s ease;
  }
  @keyframes bubbleIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

  .bubble.assistant {
    background: #181410; border: 1px solid var(--border);
    border-left: 3px solid var(--gold); margin-right: 50px;
  }
  .bubble.user {
    background: #111820; border: 1px solid #1e2a38;
    border-right: 3px solid var(--blue); margin-left: 50px;
  }
  .bubble.error {
    background: #1a0e0e; border: 1px solid #5a2828;
    border-left: 3px solid var(--red); margin-right: 50px;
  }

  .bubble-header {
    display: flex; align-items: center; gap: 6px;
    font-size: 9px; margin-bottom: 2px;
  }
  .bubble-icon { font-size: 13px; }
  .bubble-name { font-weight: bold; font-size: 9px; }
  .bubble-name.assistant { color: var(--gold); }
  .bubble-name.user { color: var(--blue); }
  .bubble-time { color: var(--text4); font-size: 8px; margin-left: auto; }
  .copy-btn {
    background: transparent; border: none; color: var(--text4);
    cursor: pointer; font-size: 12px; padding: 2px 4px;
    border-radius: 4px; transition: .15s;
  }
  .copy-btn:hover { color: var(--gold); background: var(--border); }

  .bubble-body { color: var(--text); }
  .bubble-body code {
    background: #1a1a12; color: #b8d0a0; padding: 1px 5px;
    border-radius: 3px; font-family: var(--mono); font-size: 11px;
  }
  .bubble-body pre {
    background: #1a1a12; color: #b8d0a0; padding: 10px 14px;
    border-radius: 6px; border-left: 3px solid var(--gold);
    font-family: var(--mono); font-size: 11px; overflow-x: auto;
    white-space: pre-wrap; margin: 6px 0;
  }
  .bubble-body pre code { background: none; padding: 0; }
  .bubble-body strong { color: var(--gold-light); }
  .bubble-body em { color: #a0c0b8; }
  .bubble-body ul, .bubble-body ol { padding-left: 20px; margin: 4px 0; }
  .bubble-body li { margin: 3px 0; }
  .bubble-body a { color: var(--blue); text-underline-offset: 2px; }
  .bubble-body table { border-collapse: collapse; width: 100%; font-size: 11px; margin: 6px 0; }
  .bubble-body th { background: var(--border); color: var(--gold); padding: 5px 10px; text-align: left; }
  .bubble-body td { border: 1px solid var(--border); padding: 4px 10px; }
  .bubble-body blockquote {
    border-left: 3px solid var(--gold); padding: 4px 10px;
    color: var(--text2); margin: 4px 0;
  }

  #typing-indicator {
    display: none; align-items: center; gap: 8px; padding: 12px 14px;
    background: #181410; border: 1px solid var(--border);
    border-left: 3px solid var(--gold); border-radius: var(--radius);
    margin-right: 50px; font-size: 10px; color: var(--gold);
  }
  #typing-indicator.visible { display: flex; }
  .dot-anim { display: flex; gap: 4px; }
  .dot-anim span {
    width: 6px; height: 6px; background: var(--gold); border-radius: 50%;
    animation: blink 1.2s infinite; display: block;
  }
  .dot-anim span:nth-child(2) { animation-delay: .2s; }
  .dot-anim span:nth-child(3) { animation-delay: .4s; }
  @keyframes blink { 0%,80%,100% { opacity:.2; } 40% { opacity:1; } }

  #input-area {
    background: var(--bg); border-top: 1px solid var(--border);
    flex-shrink: 0; display: flex; flex-direction: column;
  }

  #chip-strip {
    display: none; flex-wrap: wrap; gap: 6px;
    padding: 8px 20px 0; align-items: center;
  }
  #chip-strip.visible { display: flex; }

  .file-chip {
    display: flex; align-items: center; gap: 5px;
    background: var(--bg4); border: 1px solid var(--border2);
    border-radius: 14px; padding: 4px 8px 4px 10px;
    font-size: 9px; animation: chipIn .15s ease;
  }
  @keyframes chipIn { from { opacity: 0; transform: scale(.9); } to { opacity: 1; transform: none; } }
  .chip-type {
    background: var(--border2); color: var(--gold); border-radius: 4px;
    padding: 1px 5px; font-size: 7px; font-weight: bold; letter-spacing: .5px;
  }
  .chip-name { color: var(--text); max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chip-size { color: var(--text3); font-size: 8px; }
  .chip-remove {
    background: transparent; border: none; color: var(--text3);
    cursor: pointer; font-size: 14px; line-height: 1; padding: 0 2px;
    transition: .15s;
  }
  .chip-remove:hover { color: var(--red); }
  #chip-count {
    font-size: 9px; color: var(--text3); padding: 4px 4px 4px 6px;
    flex-shrink: 0;
  }

  #input-row {
    display: flex; align-items: flex-end; gap: 10px;
    padding: 12px 20px 14px;
  }

  #attach-btn {
    width: 40px; height: 40px; border-radius: 50%;
    background: var(--bg4); color: var(--gold);
    border: 1px solid var(--border2); cursor: pointer;
    font-size: 22px; line-height: 1; display: flex;
    align-items: center; justify-content: center;
    flex-shrink: 0; transition: .15s;
    position: relative; align-self: flex-end; margin-bottom: 4px;
  }
  #attach-btn:hover { background: var(--border); border-color: var(--gold); color: var(--gold-light); transform: scale(1.05); }
  #attach-btn:active { background: var(--gold); color: var(--bg); transform: scale(.96); }
  #attach-btn .badge {
    position: absolute; top: -4px; right: -4px;
    background: var(--gold); color: var(--bg); border-radius: 8px;
    font-size: 8px; padding: 1px 4px; font-weight: bold;
    display: none; line-height: 1.4;
  }
  #attach-btn.has-files .badge { display: block; }

  #attach-menu {
    position: absolute; bottom: 56px; left: 20px;
    background: var(--bg2); border: 1px solid var(--border2);
    border-radius: var(--radius); padding: 6px;
    display: none; flex-direction: column; gap: 2px;
    box-shadow: 0 4px 20px rgba(0,0,0,.6); z-index: 100; min-width: 200px;
  }
  #attach-menu.open { display: flex; }
  .attach-menu-item {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 12px; border-radius: 6px; cursor: pointer;
    font-size: 11px; color: var(--text2); transition: .12s;
  }
  .attach-menu-item:hover { background: var(--bg4); color: var(--gold); }
  .attach-menu-item .ami-icon { font-size: 16px; width: 20px; text-align: center; }
  .attach-menu-item .ami-desc { font-size: 8px; color: var(--text3); margin-top: 1px; }
  .attach-menu-sep { height: 1px; background: var(--border); margin: 3px 6px; }

  #file-input { display: none; }

  #input-box {
    flex: 1; background: var(--bg3); color: var(--text);
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 10px 14px; font-family: var(--font); font-size: 12px;
    outline: none; resize: none; line-height: 1.6;
    min-height: 44px; max-height: 160px; overflow-y: auto;
    transition: border-color .15s;
  }
  #input-box:focus { border-color: var(--gold); background: var(--bg3); }
  #input-box::placeholder { color: var(--text3); }

  #send-btn, #stop-btn {
    width: 44px; height: 44px; border-radius: var(--radius);
    border: none; cursor: pointer; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px; transition: .15s; align-self: flex-end;
  }
  #send-btn { background: var(--gold); color: var(--bg); }
  #send-btn:hover { background: var(--gold-light); }
  #send-btn:disabled { background: var(--border); color: var(--text4); cursor: default; }
  #stop-btn { background: #3a1818; color: var(--red); border: 1px solid #5a2828; display: none; }
  #stop-btn:hover { background: #4a2020; }
  #stop-btn.visible { display: flex; }

  #file-preview-modal {
    position: fixed; inset: 0; background: rgba(0,0,0,.75);
    display: none; align-items: center; justify-content: center; z-index: 200;
  }
  #file-preview-modal.open { display: flex; }
  .modal-box {
    background: var(--bg2); border: 1px solid var(--border2);
    border-radius: 12px; padding: 20px; width: 620px; max-width: 90vw;
    max-height: 80vh; display: flex; flex-direction: column; gap: 12px;
  }
  .modal-header { display: flex; align-items: center; gap: 10px; }
  .modal-title { color: var(--gold); font-size: 13px; flex: 1; }
  .modal-close {
    background: transparent; border: none; color: var(--text3);
    font-size: 18px; cursor: pointer; transition: .12s;
  }
  .modal-close:hover { color: var(--red); }
  .modal-body { flex: 1; overflow-y: auto; }
  .file-list-full { list-style: none; display: flex; flex-direction: column; gap: 6px; }
  .file-list-full li {
    display: flex; align-items: center; gap: 10px;
    background: var(--bg4); border-radius: 8px; padding: 8px 12px;
  }
  .fli-icon {
    background: var(--border2); color: var(--gold); border-radius: 5px;
    padding: 2px 6px; font-size: 8px; font-weight: bold; flex-shrink: 0;
  }
  .fli-name { flex: 1; font-size: 11px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fli-size { font-size: 9px; color: var(--text3); flex-shrink: 0; }
  .fli-remove {
    background: transparent; border: none; color: var(--text3);
    cursor: pointer; font-size: 14px; padding: 2px 4px; transition: .12s;
  }
  .fli-remove:hover { color: var(--red); }
  .modal-footer { display: flex; justify-content: flex-end; gap: 8px; }
  .btn-modal {
    background: var(--bg4); color: var(--gold); border: 1px solid var(--border2);
    border-radius: 6px; padding: 7px 14px; font-family: var(--font);
    font-size: 10px; cursor: pointer; transition: .15s;
  }
  .btn-modal:hover { background: var(--border); border-color: var(--gold); }
  .btn-modal.primary { background: var(--gold); color: var(--bg); border-color: var(--gold); }
  .btn-modal.primary:hover { background: var(--gold-light); }

  #toast {
    position: fixed; bottom: 80px; left: 50%; transform: translateX(-50%);
    background: var(--border2); color: var(--gold); border: 1px solid var(--gold);
    border-radius: 20px; padding: 8px 18px; font-size: 11px;
    opacity: 0; pointer-events: none; transition: opacity .3s; z-index: 300;
  }
  #toast.show { opacity: 1; }

  .notice {
    text-align: center; font-size: 9px; color: var(--blue);
    padding: 4px; letter-spacing: .5px;
  }

  body.drag-over #input-area {
    border-top: 2px dashed var(--gold); background: #16130e;
  }
  .drag-hint {
    display: none; position: fixed; inset: 0;
    background: rgba(14,12,10,.85); z-index: 150;
    align-items: center; justify-content: center; flex-direction: column;
    gap: 12px; color: var(--gold); font-size: 22px; pointer-events: none;
  }
  body.drag-over .drag-hint { display: flex; }
  .drag-hint small { font-size: 12px; color: var(--text2); }
</style>
</head>
<body>

<div class="drag-hint">
  📎
  <span>Drop files to attach</span>
  <small>Documents, code, data files — no images or videos</small>
</div>

<aside id="sidebar">
  <div class="logo" id="brand-name">DIGI VALET</div>
  <div class="tagline" id="brand-tagline">PERSONAL ASSISTANT</div>

  <div class="section-label">Model</div>
  <select id="model-select">
    <option>llama3</option>
    <option>mistral</option>
    <option>gemma3</option>
    <option>phi3</option>
    <option>codellama</option>
    <option>deepseek-r1</option>
  </select>

  <div class="section-label">Tone</div>
  <select id="tone-select">
    <option value="balanced">Balanced</option>
    <option value="formal">Formal</option>
    <option value="casual">Casual</option>
  </select>

  <div class="section-label">Language</div>
  <select id="lang-select">
    <option>English</option><option>Hindi</option><option>Spanish</option>
    <option>French</option><option>German</option><option>Arabic</option>
    <option>Japanese</option><option>Portuguese</option>
  </select>

  <div class="section-label">Intelligence</div>
  <div class="btn-row">
    <button class="btn-icon" id="pred-btn" onclick="toggleMode('predictive',this)">⚡ Predictive</button>
    <button class="btn-icon" id="data-btn" onclick="toggleMode('dataMode',this)">📊 Data</button>
  </div>
  <button class="btn-icon" id="nlp-btn" onclick="toggleMode('nlpMode',this)" style="width:100%;margin-top:4px;">🧠 NLP Nuance</button>

  <button class="btn-sidebar" onclick="newChat()">＋  New Conversation</button>

  <div class="section-label">My Tasks</div>
  <ul id="task-list"></ul>
  <div class="task-input-row">
    <input id="task-input" placeholder="Add task… (Enter)" onkeydown="if(event.key==='Enter')addTask()"/>
    <button onclick="addTask()">+</button>
  </div>

  <div class="section-label">Past Conversations</div>
  <ul id="history-list"></ul>

  <div class="btn-row" style="margin-top:8px;">
    <button class="btn-icon" id="theme-btn" onclick="toggleTheme()">☀ Light</button>
    <button class="btn-icon" id="priv-btn" onclick="togglePrivacy()" title="Privacy mode">🔒 Private</button>
  </div>

  <button class="btn-icon" style="width:100%;margin-top:6px;color:var(--red);border-color:var(--border);"
    onclick="clearAllChats()">Clear All History</button>

  <div class="status-dot" id="status-lbl">● Ready (browser mode)</div>
</aside>

<div id="main">

  <div id="header">
    <span id="conv-title">New Conversation</span>
    <button class="header-btn" onclick="document.getElementById('file-preview-modal').classList.add('open')">📎 Files</button>
    <button class="header-btn" id="font-minus" onclick="changeFontSize(-1)">A−</button>
    <button class="header-btn" id="font-plus"  onclick="changeFontSize(+1)">A+</button>
    <button class="header-btn" onclick="exportChat()">⬇ Export</button>
    <span id="model-indicator">—</span>
  </div>

  <div id="cmd-bar">
    <button class="cmd-chip" onclick="runCmd('/help')">/help</button>
    <button class="cmd-chip" onclick="runCmd('/tasks')">/tasks</button>
    <button class="cmd-chip" onclick="runCmd('/plan')">/plan</button>
    <button class="cmd-chip" onclick="runCmd('/wellness')">/wellness</button>
    <button class="cmd-chip" onclick="runCmd('/meal')">/meal</button>
    <button class="cmd-chip" onclick="runCmd('/summarize')">/summarize</button>
    <button class="cmd-chip" onclick="runCmd('/focus')">/focus</button>
    <button class="cmd-chip" onclick="runCmd('/analyze')">/analyze</button>
    <button class="cmd-chip" onclick="runCmd('/digivalet')">/digivalet</button>
  </div>

  <div id="chat-scroll"></div>

  <div id="typing-indicator">
    <span style="color:var(--gold);font-size:14px;">◈</span>
    <span id="typing-text">Digi Valet is thinking</span>
    <div class="dot-anim"><span></span><span></span><span></span></div>
  </div>

  <div id="input-area" style="position:relative;">

    <div id="attach-menu">
      <div class="attach-menu-item" onclick="triggerFilePicker('.txt,.md,.rst,.rtf,.log,.csv,.tsv,.json,.jsonl,.xml,.yaml,.yml,.toml,.ini,.cfg,.conf,.env')">
        <span class="ami-icon">📄</span>
        <div>
          <div>Documents &amp; Data</div>
          <div class="ami-desc">.txt .md .csv .json .xml .yaml .log …</div>
        </div>
      </div>
      <div class="attach-menu-item" onclick="triggerFilePicker('.py,.js,.ts,.jsx,.tsx,.html,.htm,.css,.java,.c,.cpp,.h,.hpp,.cs,.go,.rs,.rb,.php,.swift,.kt,.sh,.bash,.sql,.r')">
        <span class="ami-icon">💻</span>
        <div>
          <div>Code Files</div>
          <div class="ami-desc">.py .js .ts .java .c .go .sql …</div>
        </div>
      </div>
      <div class="attach-menu-item" onclick="triggerFilePicker('.pdf')">
        <span class="ami-icon">📑</span>
        <div>
          <div>PDF Document</div>
          <div class="ami-desc">.pdf (text content extracted)</div>
        </div>
      </div>
      <div class="attach-menu-sep"></div>
      <div class="attach-menu-item" onclick="triggerFilePicker('*')">
        <span class="ami-icon">📁</span>
        <div>
          <div>Browse All</div>
          <div class="ami-desc">Any supported file type</div>
        </div>
      </div>
    </div>

    <input type="file" id="file-input" multiple accept="" onchange="handleFileInput(this)"/>

    <div id="chip-strip">
      <span id="chip-count"></span>
    </div>

    <div id="input-row">
      <button id="attach-btn" onclick="toggleAttachMenu(event)" title="Attach files (up to 100)">
        <span>+</span>
        <span class="badge" id="attach-badge">0</span>
      </button>

      <textarea id="input-box" rows="1"
        placeholder="Ask Digi Valet…  Enter=send  Shift+Enter=new line  /help for commands"
        onkeydown="handleInputKey(event)" oninput="autoResize(this)"></textarea>

      <button id="send-btn" onclick="sendMessage()" title="Send (Enter)">↑</button>
      <button id="stop-btn" onclick="stopGeneration()" title="Stop">■</button>
    </div>
  </div>
</div>

<div id="file-preview-modal">
  <div class="modal-box">
    <div class="modal-header">
      <span class="modal-title">📎 Attached Files</span>
      <button class="modal-close" onclick="document.getElementById('file-preview-modal').classList.remove('open')">×</button>
    </div>
    <div class="modal-body">
      <ul class="file-list-full" id="modal-file-list"></ul>
      <p id="modal-empty" style="color:var(--text3);font-size:11px;padding:10px 0;">No files attached yet. Click + to add files.</p>
    </div>
    <div class="modal-footer">
      <button class="btn-modal" onclick="attachedFiles=[]; renderChips(); document.getElementById('file-preview-modal').classList.remove('open');">Clear All</button>
      <button class="btn-modal primary" onclick="document.getElementById('file-preview-modal').classList.remove('open')">Done</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
// ══════════════════════════════════════════════════════════════════════════════
//  STATE
// ══════════════════════════════════════════════════════════════════════════════
const MAX_FILES     = 100;
const MAX_FILE_BYTES = 5 * 1024 * 1024;
const ALLOWED_EXTS  = new Set([
  '.txt','.md','.markdown','.rst','.rtf',
  '.py','.js','.ts','.jsx','.tsx','.html','.htm','.css',
  '.java','.c','.cpp','.h','.hpp','.cs','.go','.rs',
  '.rb','.php','.swift','.kt','.sh','.bash','.zsh',
  '.sql','.r','.m','.scala','.pl','.lua',
  '.csv','.tsv','.json','.jsonl','.xml','.yaml','.yml','.toml',
  '.pdf',
  '.ini','.cfg','.conf','.env','.log',
]);

let attachedFiles   = [];
let messages        = [];
let allChats        = {};
let currentChatId   = null;
let tasks           = [];
let privacyMode     = false;
let darkMode        = true;
let fontSizeDelta   = 0;
let generating      = false;
let currentAccum    = '';
let currentBubble   = null;
let modes           = { predictive: false, dataMode: false, nlpMode: false };

// ══════════════════════════════════════════════════════════════════════════════
//  ORG TREE  (talks to the Python /api/orgtree endpoint)
// ══════════════════════════════════════════════════════════════════════════════
async function handleDigiValetLookup(query) {
  appendBubble('user', '/digivalet ' + query);
  messages.push({ role: 'user', content: '/digivalet ' + query });
  allChats[currentChatId].messages = [...messages];
  if (!privacyMode) saveChats();
  renderHistoryList();
  showTyping(true);
  try {
    const resp = await fetch('/api/orgtree?q=' + encodeURIComponent(query));
    const data = await resp.json();
    hideTyping();
    const md = data.result || '⚠ No result returned.';
    appendBubble('assistant', md);
    messages.push({ role: 'assistant', content: md });
    allChats[currentChatId].messages = [...messages];
    if (!privacyMode) saveChats();
  } catch(e) {
    hideTyping();
    appendBubble('error',
      '⚠ Could not reach the org-tree API.\n\n' +
      'Make sure you started Digi Valet via `python digi_valet.py`.');
  }
  scrollBottom();
}

// ══════════════════════════════════════════════════════════════════════════════
//  INIT
// ══════════════════════════════════════════════════════════════════════════════
window.onload = () => {
  loadPrefs();
  loadChats();
  loadTasks();
  if (Object.keys(allChats).length) {
    const id = Object.keys(allChats).sort().reverse()[0];
    switchChat(id);
  } else { newChat(); }
  renderHistoryList();
  document.getElementById('model-indicator').textContent = document.getElementById('model-select').value;
  document.getElementById('model-select').onchange = e =>
    document.getElementById('model-indicator').textContent = e.target.value;
};

// ══════════════════════════════════════════════════════════════════════════════
//  SYSTEM PROMPT
// ══════════════════════════════════════════════════════════════════════════════
const TONE = {
  formal:   "You are Digi Valet, a refined, professional, and articulate personal AI assistant. Use formal, elegant language. Be precise and thorough. Never break character.",
  balanced: "You are Digi Valet, a capable and friendly personal AI assistant. Be clear, concise, warm, and professional. Never break character.",
  casual:   "You are Digi Valet, a smart and easygoing personal AI assistant. Be friendly, direct, and conversational. Never break character.",
};
const LANG_ADDON = {
  English:'', Hindi:'Always respond in Hindi (Devanagari script).\n',
  Spanish:'Always respond in Spanish.\n', French:'Always respond in French.\n',
  German:'Always respond in German.\n',  Arabic:'Always respond in Arabic.\n',
  Japanese:'Always respond in Japanese.\n', Portuguese:'Always respond in Portuguese.\n',
};
function getSystemPrompt() {
  const tone = document.getElementById('tone-select').value;
  const lang = document.getElementById('lang-select').value;
  let p = TONE[tone] + '\n' + LANG_ADDON[lang];
  if (modes.predictive) p += "\nAfter each response add '**What you might want next:**' with 2–3 follow-up suggestions.\n";
  if (modes.dataMode)   p += "\nWhen given data, structure: Summary → Key Findings → Recommendations. Use Markdown tables.\n";
  if (modes.nlpMode)    p += "\nBriefly acknowledge the user's apparent intent or emotional state before answering.\n";
  return p;
}

// ══════════════════════════════════════════════════════════════════════════════
//  FILE ATTACHMENT
// ══════════════════════════════════════════════════════════════════════════════
let _pendingAccept = '';

function toggleAttachMenu(e) {
  e.stopPropagation();
  const menu = document.getElementById('attach-menu');
  menu.classList.toggle('open');
}
document.addEventListener('click', () => document.getElementById('attach-menu').classList.remove('open'));

function triggerFilePicker(accept) {
  document.getElementById('attach-menu').classList.remove('open');
  _pendingAccept = accept;
  const fi = document.getElementById('file-input');
  fi.accept = accept === '*' ? '' : accept;
  fi.click();
}

async function handleFileInput(input) {
  const files = Array.from(input.files);
  input.value = '';
  await processFiles(files);
}

async function processFiles(files) {
  const errors = [];
  for (const file of files) {
    if (attachedFiles.length >= MAX_FILES) { errors.push(`Limit of ${MAX_FILES} files reached.`); break; }
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!ALLOWED_EXTS.has(ext)) {
      errors.push(`'${file.name}' skipped — '${ext}' not supported.`); continue;
    }
    if (file.size > MAX_FILE_BYTES) {
      errors.push(`'${file.name}' skipped — ${(file.size/1024/1024).toFixed(1)} MB exceeds 5 MB limit.`); continue;
    }
    if (attachedFiles.find(f => f.name === file.name)) continue;
    try {
      const content = await readFileText(file);
      attachedFiles.push({ name: file.name, ext, size: file.size, content });
    } catch(e) { errors.push(`'${file.name}' could not be read.`); }
  }
  if (errors.length) showToast(errors[0]);
  renderChips();
  renderModalList();
}

function readFileText(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = e => res(e.target.result);
    r.onerror = () => rej();
    r.readAsText(file, 'utf-8');
  });
}

function fmtSize(n) {
  if (n < 1024) return n + ' B';
  if (n < 1048576) return Math.round(n/1024) + ' KB';
  return (n/1048576).toFixed(1) + ' MB';
}

function chipType(ext) {
  if (ext === '.pdf') return 'PDF';
  if (['.csv','.tsv'].includes(ext)) return 'CSV';
  if (['.json','.jsonl','.yaml','.yml','.toml','.xml'].includes(ext)) return 'DATA';
  if (['.py','.js','.ts','.jsx','.tsx','.java','.c','.cpp','.h',
       '.go','.rs','.rb','.swift','.kt','.cs','.php','.sh','.sql','.r'].includes(ext)) return 'CODE';
  return 'TXT';
}

function renderChips() {
  const strip = document.getElementById('chip-strip');
  const badge = document.getElementById('attach-badge');
  const btn   = document.getElementById('attach-btn');
  const count = document.getElementById('chip-count');
  Array.from(strip.children).forEach(c => { if (c.id !== 'chip-count') c.remove(); });
  if (!attachedFiles.length) {
    strip.classList.remove('visible');
    badge.textContent = '0';
    btn.classList.remove('has-files');
    return;
  }
  strip.classList.add('visible');
  badge.textContent = attachedFiles.length;
  btn.classList.add('has-files');
  count.textContent = `${attachedFiles.length} file${attachedFiles.length>1?'s':''} attached`;
  attachedFiles.forEach((f, i) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.innerHTML = `
      <span class="chip-type">${chipType(f.ext)}</span>
      <span class="chip-name" title="${f.name}">${f.name.length>22 ? f.name.slice(0,22)+'…' : f.name}</span>
      <span class="chip-size">${fmtSize(f.size)}</span>
      <button class="chip-remove" title="Remove">×</button>
    `;
    chip.querySelector('.chip-remove').onclick = () => { attachedFiles.splice(i,1); renderChips(); renderModalList(); };
    strip.appendChild(chip);
  });
}

function renderModalList() {
  const ul = document.getElementById('modal-file-list');
  const empty = document.getElementById('modal-empty');
  ul.innerHTML = '';
  empty.style.display = attachedFiles.length ? 'none' : 'block';
  attachedFiles.forEach((f, i) => {
    const li = document.createElement('li');
    li.innerHTML = `
      <span class="fli-icon">${chipType(f.ext)}</span>
      <span class="fli-name" title="${f.name}">${f.name}</span>
      <span class="fli-size">${fmtSize(f.size)}</span>
      <button class="fli-remove" title="Remove">×</button>
    `;
    li.querySelector('.fli-remove').onclick = () => { attachedFiles.splice(i,1); renderChips(); renderModalList(); };
    ul.appendChild(li);
  });
}

function buildFileContext(files) {
  if (!files.length) return '';
  let out = `[${files.length} file(s) attached — read them carefully before answering]\n`;
  files.forEach((f, i) => {
    out += `\n${'─'.repeat(60)}\nFile ${i+1}: ${f.name}  (${fmtSize(f.size)})\n${'─'.repeat(60)}\n`;
    out += f.content.slice(0, 40000);
  });
  out += `\n${'─'.repeat(60)}\n[End of attached files]\n`;
  return out;
}

// ══════════════════════════════════════════════════════════════════════════════
//  DRAG & DROP
// ══════════════════════════════════════════════════════════════════════════════
let _dragCounter = 0;
document.addEventListener('dragenter', e => { e.preventDefault(); _dragCounter++; document.body.classList.add('drag-over'); });
document.addEventListener('dragleave', () => { _dragCounter--; if(_dragCounter<=0){ _dragCounter=0; document.body.classList.remove('drag-over'); }});
document.addEventListener('dragover',  e => e.preventDefault());
document.addEventListener('drop', async e => {
  e.preventDefault(); _dragCounter=0; document.body.classList.remove('drag-over');
  await processFiles(Array.from(e.dataTransfer.files));
});

// ══════════════════════════════════════════════════════════════════════════════
//  CHAT MESSAGES
// ══════════════════════════════════════════════════════════════════════════════
const CMDS = {
  '/help':     'List all available quick-commands and give a concise overview of your capabilities.',
  '/tasks':    null,
  '/plan':     'Help me plan my day. Ask about my agenda and help prioritise and schedule it.',
  '/wellness': 'Run a wellness check-in: ask about mood, energy, hydration, and sleep. Give a personalised tip.',
  '/meal':     'Suggest a healthy balanced meal plan for today. Ask about dietary restrictions first.',
  '/summarize':'Summarize our conversation so far in concise bullet points.',
  '/focus':    'Help me set a focus session. Ask what I\'m working on, set a goal and timer strategy.',
  '/analyze':  'I will share data with you. Please analyse it: provide a Summary, Key Findings, and Recommendations.',
  '/digivalet': null,
};

function runCmd(cmd) {
  if (cmd === '/digivalet') {
    const q = window.prompt('Org lookup — enter name, employee ID, dept, location, or "stats":');
    if (q === null) return;
    handleDigiValetLookup(q.trim() || 'help');
    return;
  }
  let prompt = CMDS[cmd] || '';
  if (cmd === '/tasks') {
    const summary = tasks.length
      ? tasks.map(t => (t.done?'✓ ':'○ ')+t.text).join('\n')
      : 'No tasks yet.';
    prompt = `My current tasks:\n${summary}\n\nShow me my task list and ask what I'd like to add, complete, or prioritise.`;
  }
  document.getElementById('input-box').value = prompt;
  autoResize(document.getElementById('input-box'));
  sendMessage();
}

async function sendMessage() {
  if (generating) return;
  const box   = document.getElementById('input-box');
  const text  = box.value.trim();
  if (!text) return;

  const filesSnap = [...attachedFiles];
  attachedFiles = []; renderChips();

  const fileCtx   = buildFileContext(filesSnap);
  const fullText  = fileCtx ? fileCtx + '\n' + text : text;

  if (messages.length <= 1 && allChats[currentChatId]?.title === 'New Conversation') {
    allChats[currentChatId].title = text.slice(0,30) + (text.length>30?'…':'');
    document.getElementById('conv-title').textContent = allChats[currentChatId].title;
  }

  box.value = ''; autoResize(box);

  let displayText = text;
  if (filesSnap.length) displayText = `📎 **Attached:** ${filesSnap.map(f=>f.name).join(', ')}\n\n${text}`;
  appendBubble('user', displayText);

  messages.push({ role:'user', content: fullText });
  allChats[currentChatId].messages = [...messages];
  renderHistoryList();
  if (!privacyMode) saveChats();

  showTyping(true);
  setGenerating(true);

  const model = document.getElementById('model-select').value;
  try {
    await streamOllama(model, messages);
  } catch(e) {
    hideTyping();
    const errMsg = e.message.includes('fetch') || e.message.includes('network') || e.message.includes('Failed')
      ? `⚠ Cannot connect to Ollama.\n\nMake sure Ollama is running:\n\`\`\`\nollama serve\n\`\`\`\nThen pull a model:\n\`\`\`\nollama pull ${model}\n\`\`\``
      : `⚠ ${e.message}`;
    appendBubble('error', errMsg);
  }
  setGenerating(false);
}

async function streamOllama(model, msgs) {
  const resp = await fetch('http://localhost:11434/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, messages: msgs, stream: true }),
    signal: _abortCtrl.signal,
  });
  if (!resp.ok) throw new Error(`Ollama returned HTTP ${resp.status}`);

  const reader = resp.body.getReader();
  const dec    = new TextDecoder();
  currentAccum = '';
  currentBubble = null;
  hideTyping();

  while (true) {
    const { done, value } = await reader.read();
    if (done || _abortCtrl.signal.aborted) break;
    const lines = dec.decode(value, { stream: true }).split('\n');
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const data = JSON.parse(line);
        const tok  = data?.message?.content;
        if (tok) appendToken(tok);
        if (data.done) break;
      } catch {}
    }
  }
  finaliseAssistant();
}

let _abortCtrl = new AbortController();
function stopGeneration() {
  _abortCtrl.abort();
  _abortCtrl = new AbortController();
  setGenerating(false);
  finaliseAssistant();
}

function setGenerating(v) {
  generating = v;
  document.getElementById('send-btn').disabled = v;
  document.getElementById('stop-btn').classList.toggle('visible', v);
  document.getElementById('input-box').disabled = v;
  document.getElementById('attach-btn').disabled = v;
}

function appendToken(tok) {
  currentAccum += tok;
  if (!currentBubble) {
    currentBubble = appendBubble('assistant', '');
  }
  currentBubble.querySelector('.bubble-body').innerHTML = renderMarkdown(currentAccum);
  scrollBottom();
}

function finaliseAssistant() {
  if (currentAccum) {
    messages.push({ role:'assistant', content: currentAccum });
    allChats[currentChatId].messages = [...messages];
    if (!privacyMode) saveChats();
  }
  currentAccum  = '';
  currentBubble = null;
  document.getElementById('input-box').focus();
}

// ══════════════════════════════════════════════════════════════════════════════
//  BUBBLE RENDERING
// ══════════════════════════════════════════════════════════════════════════════
function now() {
  return new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
}

function appendBubble(role, text) {
  hideTyping();
  const scroll = document.getElementById('chat-scroll');
  const div = document.createElement('div');
  div.className = `bubble ${role}`;
  const icon = role === 'assistant' ? '◈' : (role === 'user' ? '◉' : '⚠');
  const name = role === 'assistant' ? 'Digi Valet' : (role === 'user' ? 'You' : 'Error');
  div.innerHTML = `
    <div class="bubble-header">
      <span class="bubble-icon" style="color:${role==='user'?'var(--blue)':'var(--gold)'}">${icon}</span>
      <span class="bubble-name ${role}">${name}</span>
      <span class="bubble-time">${now()}</span>
      <button class="copy-btn" title="Copy" onclick="copyBubble(this)">⎘</button>
    </div>
    <div class="bubble-body" style="font-size:${13+fontSizeDelta}px;">${renderMarkdown(text)}</div>
  `;
  scroll.appendChild(div);
  scrollBottom();
  return div;
}

function copyBubble(btn) {
  const text = btn.closest('.bubble').querySelector('.bubble-body').innerText;
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✓';
    setTimeout(() => btn.textContent = '⎘', 1200);
  });
}

function showTyping(v) {
  document.getElementById('typing-indicator').classList.toggle('visible', v);
  scrollBottom();
}
function hideTyping() {
  document.getElementById('typing-indicator').classList.remove('visible');
}

function scrollBottom() {
  requestAnimationFrame(() => {
    const s = document.getElementById('chat-scroll');
    s.scrollTop = s.scrollHeight;
  });
}

// ══════════════════════════════════════════════════════════════════════════════
//  MARKDOWN RENDERER
// ══════════════════════════════════════════════════════════════════════════════
function renderMarkdown(text) {
  let t = text
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  t = t.replace(/```(\w*)\n?([\s\S]*?)```/g,(_,lang,code)=>
    `<pre><code>${code.trim()}</code></pre>`);
  t = t.replace(/`([^`]+)`/g,'<code>$1</code>');
  t = t.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  t = t.replace(/__(.+?)__/g,'<strong>$1</strong>');
  t = t.replace(/\*([^*\n]+)\*/g,'<em>$1</em>');
  t = t.replace(/_([^_\n]+)_/g,'<em>$1</em>');
  t = t.replace(/~~(.+?)~~/g,'<s>$1</s>');
  t = t.replace(/^### (.+)$/gm,'<h3 style="color:var(--gold);font-size:14px;margin:6px 0 3px;">$1</h3>');
  t = t.replace(/^## (.+)$/gm, '<h2 style="color:var(--gold);font-size:15px;margin:8px 0 4px;">$1</h2>');
  t = t.replace(/^# (.+)$/gm,  '<h1 style="color:var(--gold);font-size:16px;margin:10px 0 5px;">$1</h1>');
  t = t.replace(/^---+$/gm,'<hr style="border:none;border-top:1px solid var(--border2);margin:8px 0;"/>');
  t = t.replace(/(\|[^\n]+\|\n)(\|[-| :]+\|\n)((?:\|[^\n]+\|\n?)*)/g, (_, hdr, sep, body) => {
    const cols = hdr.trim().split('|').filter(Boolean);
    const rows = body.trim().split('\n');
    const th = cols.map(c=>`<th>${c.trim()}</th>`).join('');
    const trs = rows.map(r=>'<tr>'+r.trim().split('|').filter(Boolean).map(c=>`<td>${c.trim()}</td>`).join('')+'</tr>').join('');
    return `<table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
  });
  t = t.replace(/^&gt; (.+)$/gm,'<blockquote>$1</blockquote>');
  t = t.replace(/(^[\-\*] .+\n?)+/gm, m => {
    const items = m.match(/^[\-\*] (.+)$/gm)||[];
    return '<ul>'+items.map(i=>`<li>${i.replace(/^[\-\*] /,'')}</li>`).join('')+'</ul>';
  });
  t = t.replace(/(^\d+\. .+\n?)+/gm, m => {
    const items = m.match(/^\d+\. (.+)$/gm)||[];
    return '<ol>'+items.map(i=>`<li>${i.replace(/^\d+\. /,'')}</li>`).join('')+'</ol>';
  });
  t = t.replace(/\[([^\]]+)\]\((https?[^)]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  t = t.replace(/\n/g,'<br>');
  return t;
}

// ══════════════════════════════════════════════════════════════════════════════
//  CHAT SESSION MANAGEMENT
// ══════════════════════════════════════════════════════════════════════════════
function newChat() {
  currentChatId = 'chat_' + Date.now();
  messages      = [{ role:'system', content: getSystemPrompt() }];
  allChats[currentChatId] = { title: 'New Conversation', messages: [...messages] };
  clearChatDisplay();
  addWelcome();
  renderHistoryList();
  document.getElementById('conv-title').textContent = 'New Conversation';
  if (!privacyMode) saveChats();
}

function switchChat(id) {
  currentChatId = id;
  messages = allChats[id].messages || [{ role:'system', content: getSystemPrompt() }];
  clearChatDisplay();
  if (messages.length > 1) {
    messages.slice(1).forEach(m => appendBubble(m.role, m.content));
  } else { addWelcome(); }
  document.getElementById('conv-title').textContent = allChats[id].title || id;
  renderHistoryList();
}

function clearChatDisplay() {
  document.getElementById('chat-scroll').innerHTML = '';
}

function addWelcome() {
  appendBubble('assistant',
    'Good day. I\'m **Digi Valet**, your personal AI assistant.\n\n' +
    'Use the quick-command buttons above to get started — try `/help`, `/tasks`, `/plan`, `/wellness`, or `/digivalet` for org-chart lookups.\n\n' +
    'Click **＋** to attach documents, code, or data files for analysis.');
}

function clearAllChats() {
  if (!confirm('Clear all conversation history?')) return;
  allChats = {};
  localStorage.removeItem('dv_chats');
  newChat();
}

function renderHistoryList() {
  const ul = document.getElementById('history-list');
  ul.innerHTML = '';
  Object.keys(allChats).sort().reverse().forEach(id => {
    const li = document.createElement('li');
    li.textContent = allChats[id].title || id;
    li.title = 'Click to open  •  Double-click to rename';
    if (id === currentChatId) li.className = 'active';
    li.onclick = () => switchChat(id);
    li.ondblclick = () => {
      const n = prompt('Rename:', allChats[id].title);
      if (n?.trim()) { allChats[id].title = n.trim(); renderHistoryList(); saveChats(); }
    };
    ul.appendChild(li);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
//  TASKS
// ══════════════════════════════════════════════════════════════════════════════
function addTask() {
  const input = document.getElementById('task-input');
  const text  = input.value.trim();
  if (!text) return;
  tasks.push({ text, done: false });
  input.value = '';
  renderTasks();
  saveTasks();
}

function toggleTask(i) {
  tasks[i].done = !tasks[i].done;
  renderTasks(); saveTasks();
}

function renderTasks() {
  const ul = document.getElementById('task-list');
  ul.innerHTML = '';
  tasks.forEach((t, i) => {
    const li = document.createElement('li');
    if (t.done) li.className = 'done';
    li.innerHTML = `<span class="task-dot${t.done?' done':''}">${t.done?'✓':'○'}</span><span class="task-text">${t.text}</span>`;
    li.onclick = () => toggleTask(i);
    ul.appendChild(li);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
//  UI CONTROLS
// ══════════════════════════════════════════════════════════════════════════════
function toggleMode(mode, btn) {
  modes[mode] = !modes[mode];
  btn.classList.toggle('active', modes[mode]);
  if (messages[0]?.role === 'system') messages[0].content = getSystemPrompt();
}

function toggleTheme() {
  darkMode = !darkMode;
  applyTheme();
  savePrefs();
}

function applyTheme() {
  const r = document.documentElement.style;
  if (darkMode) {
    r.setProperty('--bg','#0e0c0a'); r.setProperty('--bg2','#141210');
    r.setProperty('--bg3','#1a1612'); r.setProperty('--bg4','#1e1a16');
    r.setProperty('--border','#2a2420'); r.setProperty('--border2','#3a3028');
    r.setProperty('--text','#d4cfc8'); r.setProperty('--text2','#8a7a6a');
    r.setProperty('--text3','#5c5046'); r.setProperty('--text4','#3a3028');
  } else {
    r.setProperty('--bg','#f5f0e8'); r.setProperty('--bg2','#ede8dc');
    r.setProperty('--bg3','#fff8ec'); r.setProperty('--bg4','#f5f0e8');
    r.setProperty('--border','#d0c8b8'); r.setProperty('--border2','#c8b870');
    r.setProperty('--text','#2a2010'); r.setProperty('--text2','#6a5a4a');
    r.setProperty('--text3','#a09080'); r.setProperty('--text4','#c0b090');
  }
  document.getElementById('theme-btn').textContent = darkMode ? '☀ Light' : '☾ Dark';
}

function togglePrivacy() {
  privacyMode = !privacyMode;
  const btn = document.getElementById('priv-btn');
  btn.classList.toggle('active', privacyMode);
  btn.textContent = privacyMode ? '🔒 ON' : '🔒 Private';
  addNotice(privacyMode
    ? '🔒 Privacy mode ON — session will not be saved.'
    : '🔓 Privacy mode OFF — history is being saved.');
}

function addNotice(text) {
  const n = document.createElement('div');
  n.className = 'notice'; n.textContent = text;
  document.getElementById('chat-scroll').appendChild(n);
  scrollBottom();
}

function changeFontSize(delta) {
  fontSizeDelta = Math.max(-4, Math.min(6, fontSizeDelta + delta));
  document.querySelectorAll('.bubble-body').forEach(b =>
    b.style.fontSize = (13 + fontSizeDelta) + 'px');
}

function exportChat() {
  if (messages.length <= 1) { showToast('No messages to export.'); return; }
  const title = allChats[currentChatId]?.title || 'conversation';
  let md = `# ${title}\n*Exported ${new Date().toLocaleString()}*\n\n---\n`;
  messages.forEach(m => {
    if (m.role === 'system') return;
    md += `**${m.role === 'assistant' ? 'Digi Valet' : 'You'}**\n\n${m.content}\n\n---\n`;
  });
  const a = document.createElement('a');
  a.href = 'data:text/markdown;charset=utf-8,' + encodeURIComponent(md);
  a.download = title.replace(/[^\w\s-]/g,'').replace(/\s+/g,'_') + '.md';
  a.click();
}

// ══════════════════════════════════════════════════════════════════════════════
//  INPUT
// ══════════════════════════════════════════════════════════════════════════════
function handleInputKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

// ══════════════════════════════════════════════════════════════════════════════
//  PERSISTENCE
// ══════════════════════════════════════════════════════════════════════════════
function saveChats() {
  try { localStorage.setItem('dv_chats', JSON.stringify(allChats)); } catch {}
}
function loadChats() {
  try { allChats = JSON.parse(localStorage.getItem('dv_chats') || '{}'); } catch { allChats = {}; }
}
function saveTasks() {
  try { localStorage.setItem('dv_tasks', JSON.stringify(tasks)); } catch {}
}
function loadTasks() {
  try { tasks = JSON.parse(localStorage.getItem('dv_tasks') || '[]'); } catch { tasks = []; }
}
function savePrefs() {
  try { localStorage.setItem('dv_prefs', JSON.stringify({ darkMode, fontSizeDelta, modes })); } catch {}
}
function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem('dv_prefs') || '{}');
    if (p.darkMode !== undefined) darkMode = p.darkMode;
    if (p.fontSizeDelta) fontSizeDelta = p.fontSizeDelta;
    if (p.modes) modes = { ...modes, ...p.modes };
    applyTheme();
  } catch {}
}

// ══════════════════════════════════════════════════════════════════════════════
//  TOAST
// ══════════════════════════════════════════════════════════════════════════════
let _toastTimer;
function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => t.classList.remove('show'), 3500);
}
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
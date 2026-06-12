"""
org_tree.py — Local Org Chart lookup for Digi Valet
-----------------------------------------------------
Loads employee data from a CSV file (exported from the OrgTree.numbers
spreadsheet) and provides simple lookup / search helpers.

Expected CSV columns (header row, exact names):
    Employee Number, Display Name, Job Title, Department, Location, Reportees Count

Place the CSV next to digi_valet_chat.py (or pass an explicit path) and it
will be picked up automatically on startup.
"""

import csv
from pathlib import Path
from typing import Optional  # Added for Python < 3.10 compatibility


class OrgTree:
    def __init__(self, csv_path: str = None):
        self.employees = {}        # emp_num -> info dict
        self.name_to_num = {}       # lowercase name -> emp_num
        self.loaded = False
        self.source_path = None
        if csv_path and Path(csv_path).exists():
            self.load_from_csv(csv_path)

    # ── Loading ──────────────────────────────────────────────────────────

    # Expected column order when fields are tab-separated inside one cell
    _FIELD_ORDER = ['emp_num', 'name', 'title', 'dept', 'location', 'reports_count']

    def _parse_row(self, row: dict) -> Optional[dict]:
        """
        Return a normalised employee dict from a csv.DictReader row.

        Handles two layouts that can appear in the same file:
        1. Normal CSV  — each field in its own column (comma-separated).
        2. Broken rows — all six fields crammed into 'Employee Number' as a
           tab-separated string (happens when new rows are pasted from a
           spreadsheet that uses tabs instead of commas).
        """
        emp_num_raw = (row.get('Employee Number') or '').strip()
        if not emp_num_raw:
            return None

        # ── Layout 1: properly separated row ─────────────────────────────
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

        # ── Layout 2: all fields tab-joined inside 'Employee Number' ─────
        # e.g. "PD036\tAnil Dhanotiya\tRegional Lead\tProjects\tIndore\t0"
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

        return None  # row has an emp_num but no other data — skip it

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

    # ── Lookups ──────────────────────────────────────────────────────────

    def get_employee(self, identifier: str):
        """Find an employee by Employee Number OR Display Name (case-insensitive)."""
        ident = (identifier or '').strip()
        if not ident:
            return None
        if ident in self.employees:
            return self.employees[ident]
        if ident.lower() in self.name_to_num:
            return self.employees[self.name_to_num[ident.lower()]]
        return None

    # Common abbreviations users may type that don't literally appear in the data
    SYNONYMS = {
        "qa": "quality assurance",
        "hr": "human resources",
        "it": "it & network support",
        "ceo": "ceo",
        "vp": "vp",
        "pm": "program manager",
    }

    def search(self, query: str, limit: int = 25):
        """Search name / title / department / location for a substring match."""
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

    def list_by_department(self, dept_query: str, limit: int = 50):
        if not self.loaded:
            return []
        q = (dept_query or '').strip().lower()
        return [i for i in self.employees.values() if q in i['dept'].lower()][:limit]

    def list_by_location(self, loc_query: str, limit: int = 50):
        if not self.loaded:
            return []
        q = (loc_query or '').strip().lower()
        return [i for i in self.employees.values() if q in i['location'].lower()][:limit]

    def stats(self):
        depts = {}
        locs = {}
        for info in self.employees.values():
            depts[info['dept']] = depts.get(info['dept'], 0) + 1
            locs[info['location']] = locs.get(info['location'], 0) + 1
        return {
            'total': len(self.employees),
            'departments': dict(sorted(depts.items(), key=lambda x: -x[1])),
            'locations': dict(sorted(locs.items(), key=lambda x: -x[1])),
        }

    # ── Formatting helpers ──────────────────────────────────────────────

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
    def format_results_table(results) -> str:
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

    def to_context_snippet(self, max_rows: int = 200) -> str:
        """A compact text block suitable for inclusion in an LLM system prompt."""
        if not self.loaded:
            return ""
        lines = ["Org Tree (Employee Number | Name | Title | Department | Location | Direct Reports):"]
        for info in list(self.employees.values())[:max_rows]:
            lines.append(
                f"{info['emp_num']} | {info['name']} | {info['title']} | "
                f"{info['dept']} | {info['location']} | {info['reports_count']}"
            )
        return "\n".join(lines)
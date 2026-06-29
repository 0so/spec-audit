"""
spec-audit v0.2 -- minimal standalone CLI, decision-ready output.

Two detection rules only, unchanged from v0 (this release is presentation
only, no new detection logic, no new heuristics beyond a trivial
"earliest declaration" ordering signal that was already implicit in the
data the engine already collects):

  IDENTICAL_SYMBOL_DUPLICATION -- the same constant NAME is declared as
      an UPPER_CASE module-level constant in more than one file in the
      target repo. Detection primitive is symbol identity, not literal
      value: an early dogfooding run against a real multi-file research
      codebase showed that matching by value alone produces overwhelming
      false-positive noise (numeric coincidence with no semantic
      relationship, e.g. two unrelated constants that both happen to
      equal 30) while still catching every real duplication present
      (a fee-rate constant declared identically in two files). Matching
      by name instead of value is the direct fix for that measured
      signal/noise ratio.

  INLINE_OVERRIDE -- a function named like a guard/validator
      (validate_/check_/is_valid_/guard_ prefix) compares against a
      bare literal instead of reading a named constant.

Explicitly NOT attempted yet: SEMANTIC_PARAMETER_CLUSTER (detecting two
differently-named constants that drive the same decision path via
shared call-sites/AST usage context) -- that requires a usage-graph
primitive, not a literal/name-matching one, and is deferred as a
separate, harder v1 rule until v0's simpler rules have proven sellable.
No spec-audit.toml, no JSON schema beyond the flat dict already used
here -- those come after seeing real output, not before.

v0.2 change (this file only -- presentation, not detection): each
violation is enriched with a "conflict" breakdown, a best-effort
"heuristic_source" guess (order-of-appearance only -- explicitly not a
correctness claim), a plain-language "risk" statement, and a single
actionable "action" sentence, each bilingual (en/es). The goal is to
turn "here is a technical fact" into "here is what you should do about
it", without touching what gets detected or why.

Usage:
    spec-audit <repo_path> [--format json|html] [--lang en|es|both]
"""

from __future__ import annotations

import ast
import html as html_lib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

_GUARD_NAME_PREFIXES: tuple[str, ...] = ("validate_", "check_", "is_valid_", "guard_")
_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {".git", "__pycache__", ".venv", "venv", "node_modules"}
)


@dataclass
class Constant:
    name: str
    value: object
    file: str
    lineno: int


@dataclass
class Violation:
    rule: str  # "IDENTICAL_SYMBOL_DUPLICATION" | "INLINE_OVERRIDE"
    file: str
    lineno: int
    message: str
    symbol: str | None = None
    conflict: list[dict] | None = None
    heuristic_source: str | None = None
    risk: dict | None = None  # {"en": ..., "es": ...}
    action: dict | None = None  # {"en": ..., "es": ...}

    def to_dict(self) -> dict:
        d = {"rule": self.rule, "file": self.file, "line": self.lineno, "message": self.message}
        if self.symbol is not None:
            d["symbol"] = self.symbol
        if self.conflict is not None:
            d["conflict"] = self.conflict
        if self.heuristic_source is not None:
            d["heuristic_source"] = self.heuristic_source
        if self.risk is not None:
            d["risk"] = self.risk
        if self.action is not None:
            d["action"] = self.action
        return d


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "violation_count": len(self.violations),
            "violations": [v.to_dict() for v in self.violations],
        }


def _iter_python_files(root: Path) -> list[Path]:
    out = []
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        out.append(path)
    return out


def _literal_value(node: ast.expr | None) -> object | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool)):
        return node.value
    return None


def _extract_constants(path: Path) -> list[Constant]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    constants: list[Constant] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id.isupper():
                value = _literal_value(node.value)
                if value is not None:
                    constants.append(Constant(node.target.id, value, str(path), node.lineno))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    value = _literal_value(node.value)
                    if value is not None:
                        constants.append(Constant(target.id, value, str(path), node.lineno))
    return constants


def check_identical_symbol_duplication(all_files: list[Path]) -> list[Violation]:
    by_name: dict[str, list[Constant]] = {}
    for path in all_files:
        for c in _extract_constants(path):
            by_name.setdefault(c.name, []).append(c)

    violations: list[Violation] = []
    for sites in by_name.values():
        distinct_files = {s.file for s in sites}
        if len(distinct_files) < 2:
            continue

        # heuristic_source: order-of-appearance only (earliest site found
        # during the filesystem walk). This is NOT a correctness signal --
        # it is disclosed as such in the output, never silently implied.
        canonical = sites[0]
        conflict = [{"file": s.file, "symbol": s.name, "value": s.value} for s in sites]
        values_diverged = len({s.value for s in sites}) > 1

        for dup in sites[1:]:
            risk_en = (
                "Backtest or production results may differ depending on which "
                "declaration site is actually imported on a given execution "
                "path -- the reported metric and the executed code may not "
                "agree on the value of this parameter."
                if values_diverged
                else "These two declarations currently agree, but nothing in "
                "the code enforces that -- a future edit to either site can "
                "silently desynchronize them without any error."
            )
            risk_es = (
                "Los resultados del backtest o de producción pueden variar "
                "según qué sitio de declaración se importe realmente en una "
                "ruta de ejecución dada -- la métrica reportada y el código "
                "ejecutado pueden no coincidir en el valor de este parámetro."
                if values_diverged
                else "Estas dos declaraciones coinciden hoy, pero nada en el "
                "código obliga a que sigan así -- un cambio futuro en "
                "cualquiera de los dos sitios puede desincronizarlas "
                "silenciosamente, sin ningún error."
            )
            action_en = f"Consolidate into a single canonical definition of '{dup.name}'."
            action_es = f"Consolida '{dup.name}' en una única definición canónica."

            violations.append(
                Violation(
                    rule="IDENTICAL_SYMBOL_DUPLICATION",
                    file=dup.file,
                    lineno=dup.lineno,
                    message=(
                        f"constant '{dup.name}' = {dup.value!r} is also declared at "
                        f"{canonical.file}:{canonical.lineno} (value there: "
                        f"{canonical.value!r}) -- no single declaration site for "
                        f"this symbol"
                    ),
                    symbol=dup.name,
                    conflict=conflict,
                    heuristic_source=(
                        f"{canonical.file}:{canonical.lineno} (earliest declaration found "
                        f"-- order-of-appearance only, not a correctness claim)"
                    ),
                    risk={"en": risk_en, "es": risk_es},
                    action={"en": action_en, "es": action_es},
                )
            )
    return violations


def check_inline_override(all_files: list[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in all_files:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith(_GUARD_NAME_PREFIXES):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Compare):
                    for side in (inner.left, *inner.comparators):
                        literal = _literal_value(side)
                        if literal is not None:
                            violations.append(
                                Violation(
                                    rule="INLINE_OVERRIDE",
                                    file=str(path),
                                    lineno=inner.lineno,
                                    message=(
                                        f"guard function '{node.name}' compares against "
                                        f"inline literal {literal!r} instead of a named "
                                        f"constant"
                                    ),
                                    symbol=node.name,
                                    conflict=[
                                        {
                                            "file": str(path),
                                            "symbol": node.name,
                                            "value": literal,
                                        }
                                    ],
                                    heuristic_source="unknown (no named constant referenced at this comparison)",
                                    risk={
                                        "en": (
                                            "This guard's threshold is not traceable to a "
                                            "single named source -- if the real limit "
                                            "changes elsewhere, this check keeps enforcing "
                                            "the old, now-incorrect value with no warning."
                                        ),
                                        "es": (
                                            "El umbral de este guard no es trazable a una "
                                            "única fuente con nombre -- si el límite real "
                                            "cambia en otro lugar, este chequeo sigue "
                                            "aplicando el valor antiguo, ya incorrecto, sin "
                                            "ningún aviso."
                                        ),
                                    },
                                    action={
                                        "en": (
                                            f"Replace the literal {literal!r} in "
                                            f"'{node.name}' with a reference to its named "
                                            f"constant."
                                        ),
                                        "es": (
                                            f"Reemplaza el literal {literal!r} en "
                                            f"'{node.name}' por una referencia a su "
                                            f"constante con nombre."
                                        ),
                                    },
                                )
                            )
    return violations


def run(repo_path: Path) -> Report:
    all_files = _iter_python_files(repo_path)
    report = Report()
    report.violations.extend(check_identical_symbol_duplication(all_files))
    report.violations.extend(check_inline_override(all_files))
    return report


def _risk_level(violation_dict: dict) -> str:
    """Presentation-only heuristic for the HTML view's color tag.
    Not part of the JSON output, not a detection rule."""
    if violation_dict["rule"] == "IDENTICAL_SYMBOL_DUPLICATION":
        values = {c["value"] for c in violation_dict.get("conflict", [])}
        return "high" if len(values) > 1 else "medium"
    return "medium"


def _render_html(report_dict: dict) -> str:
    cards = []
    for v in report_dict["violations"]:
        level = _risk_level(v)
        conflict_rows = "".join(
            f"<li><code>{html_lib.escape(c['file'])}</code> &rarr; "
            f"<code>{html_lib.escape(c['symbol'])} = {html_lib.escape(str(c['value']))}</code></li>"
            for c in v.get("conflict", [])
        )
        risk = v.get("risk", {})
        action = v.get("action", {})
        cards.append(
            f"""
<div class="card risk-{level}">
  <div class="card-header">
    <span class="badge badge-{level}">{level.upper()}</span>
    <span class="rule">{html_lib.escape(v['rule'])}</span>
    <span class="loc">{html_lib.escape(v['file'])}:{v['line']}</span>
  </div>
  <p class="message">{html_lib.escape(v['message'])}</p>
  <div class="conflict"><strong>Conflict / Conflicto:</strong><ul>{conflict_rows}</ul></div>
  <div class="heuristic"><strong>Likely source / Fuente probable:</strong>
    {html_lib.escape(v.get('heuristic_source', 'unknown'))}</div>
  <div class="risk"><strong>Risk / Riesgo (EN):</strong> {html_lib.escape(risk.get('en', ''))}<br>
  <strong>Riesgo (ES):</strong> {html_lib.escape(risk.get('es', ''))}</div>
  <div class="action"><strong>What to do next / Qué hacer ahora:</strong><br>
    EN: {html_lib.escape(action.get('en', ''))}<br>
    ES: {html_lib.escape(action.get('es', ''))}</div>
</div>"""
        )

    body = "".join(cards) if cards else "<p>No violations found. / No se encontraron violaciones.</p>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>spec-audit report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background: #0d1117; color: #c9d1d9; padding: 2rem; max-width: 900px; margin: auto; }}
  h1 {{ color: #58a6ff; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }}
  .risk-high {{ border-left: 4px solid #f85149; }}
  .risk-medium {{ border-left: 4px solid #d29922; }}
  .risk-low {{ border-left: 4px solid #3fb950; }}
  .badge {{ font-size: 0.7rem; font-weight: bold; padding: 0.15rem 0.5rem; border-radius: 4px; margin-right: 0.5rem; }}
  .badge-high {{ background: #f85149; color: #0d1117; }}
  .badge-medium {{ background: #d29922; color: #0d1117; }}
  .badge-low {{ background: #3fb950; color: #0d1117; }}
  .rule {{ font-weight: bold; color: #58a6ff; }}
  .loc {{ color: #8b949e; float: right; }}
  code {{ background: #21262d; padding: 0.1rem 0.3rem; border-radius: 3px; }}
  .conflict, .heuristic, .risk, .action {{ margin-top: 0.5rem; font-size: 0.92rem; }}
  ul {{ margin: 0.25rem 0 0.25rem 1.25rem; }}
</style>
</head>
<body>
<h1>spec-audit report</h1>
<p>{report_dict['violation_count']} violation(s) found / encontrada(s).</p>
{body}
</body>
</html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    fmt = "json"
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--format":
            if i + 1 >= len(argv):
                print("usage: spec-audit <repo_path> [--format json|html]", file=sys.stderr)
                return 2
            fmt = argv[i + 1]
            i += 2
            continue
        positional.append(arg)
        i += 1

    if len(positional) != 1 or fmt not in ("json", "html"):
        print("usage: spec-audit <repo_path> [--format json|html]", file=sys.stderr)
        return 2

    repo_path = Path(positional[0])
    report = run(repo_path)
    report_dict = report.to_dict()

    if fmt == "html":
        print(_render_html(report_dict))
    else:
        print(json.dumps(report_dict, indent=2, ensure_ascii=False))

    return 1 if report_dict["violation_count"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

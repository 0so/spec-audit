"""
spec-audit v0 -- minimal standalone CLI.

Two rules only, no config file, no schema design yet (deliberate -- this
is a dogfooding probe, not a finished product):

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

Usage:
    python3 spec_audit/cli.py <repo_path>
"""

from __future__ import annotations

import ast
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
    rule: str  # "DUPLICATE_DECLARATION" | "INLINE_OVERRIDE"
    file: str
    lineno: int
    message: str


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "violation_count": len(self.violations),
            "violations": [
                {"rule": v.rule, "file": v.file, "line": v.lineno, "message": v.message}
                for v in self.violations
            ],
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
        canonical = sites[0]
        for dup in sites[1:]:
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
                                )
                            )
    return violations


def run(repo_path: Path) -> Report:
    all_files = _iter_python_files(repo_path)
    report = Report()
    report.violations.extend(check_identical_symbol_duplication(all_files))
    report.violations.extend(check_inline_override(all_files))
    return report


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: spec-audit <repo_path>", file=sys.stderr)
        return 2
    repo_path = Path(argv[0])
    report = run(repo_path)
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 1 if report.to_dict()["violation_count"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

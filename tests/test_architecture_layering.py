from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


@dataclass(frozen=True)
class ImportFinding:
    module: str
    imported: str
    line: int


DOMAIN_FORBIDDEN_IMPORTS = {
    "quiniela.adapters",
    "quiniela.db",
    "quiniela.infrastructure",
    "requests",
    "sqlite3",
    "typer",
}
PORTS_FORBIDDEN_IMPORTS = {
    "quiniela.adapters",
    "quiniela.db",
}
APPLICATION_FORBIDDEN_IMPORTS = {
    "quiniela.adapters",
    "quiniela.api_football_client",
    "requests",
    "rich",
    "sqlite3",
    "typer",
}

def test_domain_has_no_adapter_or_infrastructure_imports() -> None:
    violations = _forbidden_imports("quiniela/domain", DOMAIN_FORBIDDEN_IMPORTS)

    assert violations == []


def test_ports_do_not_import_adapters_or_legacy_db() -> None:
    violations = _forbidden_imports("quiniela/ports", PORTS_FORBIDDEN_IMPORTS)

    assert violations == []


def test_application_does_not_import_concrete_providers_or_ui() -> None:
    violations = _forbidden_imports(
        "quiniela/application",
        APPLICATION_FORBIDDEN_IMPORTS,
    )

    assert violations == []


def _forbidden_imports(
    package_path: str,
    forbidden: set[str],
    *,
    allowed: set[tuple[str, str]] | None = None,
) -> list[tuple[str, int, str]]:
    allowed = allowed or set()
    violations: list[tuple[str, int, str]] = []
    for path in sorted((SRC_ROOT / package_path).rglob("*.py")):
        module = _module_name(path)
        for finding in _imports_from(path, module):
            if (finding.module, finding.imported) in allowed:
                continue
            if _matches_forbidden(finding.imported, forbidden):
                violations.append((finding.module, finding.line, finding.imported))
    return violations


def _imports_from(path: Path, module: str) -> list[ImportFinding]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = ImportVisitor(module)
    visitor.visit(tree)
    return visitor.findings


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC_ROOT).with_suffix("").parts)


def _matches_forbidden(imported: str, forbidden: set[str]) -> bool:
    return any(imported == item or imported.startswith(f"{item}.") for item in forbidden)


class ImportVisitor(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.findings: list[ImportFinding] = []
        self._type_checking_depth = 0

    def visit_If(self, node: ast.If) -> None:
        is_type_checking = _is_type_checking_guard(node.test)
        if is_type_checking:
            self._type_checking_depth += 1
        for child in node.body:
            self.visit(child)
        if is_type_checking:
            self._type_checking_depth -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_Import(self, node: ast.Import) -> None:
        if self._type_checking_depth:
            return
        for alias in node.names:
            self.findings.append(
                ImportFinding(
                    module=self.module,
                    imported=alias.name,
                    line=node.lineno,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._type_checking_depth or node.module is None:
            return
        self.findings.append(
            ImportFinding(
                module=self.module,
                imported=node.module,
                line=node.lineno,
            )
        )


def _is_type_checking_guard(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id == "typing" and node.attr == "TYPE_CHECKING"
    return False

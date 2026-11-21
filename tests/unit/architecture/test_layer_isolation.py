"""
Test that layers are properly isolated.

Domain layer must have NO dependencies on infrastructure or API layers.
This prevents circular dependencies and keeps domain logic pure.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

# Anchor to the repo root so the suite works from any cwd.
ROOT = Path(__file__).resolve().parents[3]
MODULES = ROOT / "app" / "modules"

logger = logging.getLogger(__name__)


class ImportVisitor(ast.NodeVisitor):
    """Extract import statements from AST, excluding TYPE_CHECKING imports."""

    def __init__(self) -> None:
        self.imports: set[str] = set()
        self._in_type_checking: bool = False

    def visit_If(self, node: ast.If) -> Any:
        """Track if we're inside a TYPE_CHECKING block."""
        # Check if this is a TYPE_CHECKING conditional
        is_type_checking = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"

        if is_type_checking:
            # Skip visiting imports inside TYPE_CHECKING block
            return None

        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        if node.module:
            self.imports.add(node.module)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            self.imports.add(alias.name)
        self.generic_visit(node)


def _get_imports(filepath: Path) -> set[str]:
    """Extract all module imports from a Python file."""
    try:
        tree = ast.parse(filepath.read_text())
        visitor = ImportVisitor()
        visitor.visit(tree)
        return visitor.imports
    except Exception:
        return set()


def test_domain_layer_never_imports_infrastructure() -> None:
    """Domain code must not import from infrastructure or API layers."""
    violations: list[str] = []
    forbidden_patterns = [
        "infrastructure",
        "postgres",
        "sqlalchemy",
        "api.http",
        "api.routes",
        "api.deps",
        "api.middleware",
    ]

    domain_files = list(MODULES.glob("*/domain/**/*.py"))
    assert domain_files, "No domain files found"

    for filepath in domain_files:
        imports = _get_imports(filepath)

        for imp in imports:
            if any(forbidden in imp for forbidden in forbidden_patterns):
                violations.append(f"{filepath}: imports {imp}")

    assert not violations, "Domain layer imports infrastructure/API layers:\n" + "\n".join(
        violations
    )


def test_domain_layer_never_imports_application() -> None:
    """Domain code must not import from application layer (no upward dependencies)."""
    violations: list[str] = []

    domain_files = list(MODULES.glob("*/domain/**/*.py"))

    for filepath in domain_files:
        imports = _get_imports(filepath)

        for imp in imports:
            # Check for application layer imports
            if ".application." in imp and "app.modules." in imp:
                violations.append(f"{filepath}: imports {imp}")

    assert not violations, "Domain layer imports application layer:\n" + "\n".join(violations)


def test_common_non_api_modules_do_not_import_other_bounded_contexts() -> None:
    """Common code outside API wiring must not depend on bounded-context packages."""
    violations: list[str] = []

    common_files = [
        filepath
        for filepath in (MODULES / "common").glob("**/*.py")
        if "/api/" not in filepath.as_posix()
    ]

    for filepath in common_files:
        imports = _get_imports(filepath)

        for imp in imports:
            if not imp.startswith("app.modules"):
                continue

            if not imp.startswith("app.modules.common") and not imp.startswith("app.config"):
                violations.append(f"{filepath}: imports {imp}")

    assert not violations, "Common non-API modules import bounded-context packages:\n" + "\n".join(
        violations
    )


def test_infrastructure_layer_never_imports_api_routes() -> None:
    """Infrastructure should not import from API routes (no circular deps)."""
    violations: list[str] = []

    infra_files = list(MODULES.glob("*/infrastructure/**/*.py"))

    for filepath in infra_files:
        imports = _get_imports(filepath)

        for imp in imports:
            if "api.routes" in imp or "api.http" in imp:
                violations.append(f"{filepath}: imports {imp}")

    assert not violations, "Infrastructure imports API routes:\n" + "\n".join(violations)


def test_domain_layer_has_no_print_or_logging() -> None:
    """Domain code must not call print() or import the logging module.

    Domain is pure; side effects (logging, print) belong in application/infrastructure.
    """
    violations: list[str] = []

    domain_files = list(MODULES.glob("*/domain/**/*.py"))
    assert domain_files, "No domain files found"

    for filepath in domain_files:
        imports = _get_imports(filepath)

        # AST-level check: logging module imported in domain
        if "logging" in imports:
            violations.append(f"{filepath}: imports logging module")

        try:
            tree = ast.parse(filepath.read_text())
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                violations.append(f"{filepath}:{node.lineno}: print() call")

    assert not violations, "Domain layer must not import logging or call print():\n" + "\n".join(
        violations
    )


def test_domain_layer_never_generates_ids() -> None:
    """Domain layer must not generate UUIDs or IDs.

    Per rules.md §2: the application layer owns identity creation via IdGenerator.
    Domain code must receive IDs as arguments, never generate them.
    """
    violations: list[str] = []

    # The id_generator.py defines the generator — it is the allowed source.
    allowed_filenames = {"id_generator.py"}

    domain_files = list(MODULES.glob("*/domain/**/*.py"))
    assert domain_files, "No domain files found"

    for filepath in domain_files:
        if filepath.name in allowed_filenames:
            continue

        imports = _get_imports(filepath)
        content = filepath.read_text()

        # Flag imports of uuid generation utilities
        uuid_imports = [imp for imp in imports if "uuid" in imp.lower() and imp not in ("uuid",)]
        # Only flag if they're actually using generation functions
        for imp in uuid_imports:
            if any(fn in content for fn in ("uuid4()", "uuid1()", "uuid7()", "new_uuid7()")):
                violations.append(
                    f"{filepath}: imports uuid generator ({imp}) and calls generation"
                )
                break

        # Direct calls without import (e.g. from already-imported module)
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # IdGenerator.new_uuid7() or DomainIdGenerator.new_uuid7()
            if isinstance(node.func, ast.Attribute) and node.func.attr == "new_uuid7":
                violations.append(
                    f"{filepath}:{node.lineno}: ID generated in domain via {ast.unparse(node.func)}()"
                )
            # uuid4(), uuid7() bare calls
            if isinstance(node.func, ast.Name) and node.func.id in ("uuid4", "uuid7", "uuid1"):
                violations.append(
                    f"{filepath}:{node.lineno}: ID generated in domain via {node.func.id}()"
                )

    assert not violations, (
        "Domain layer must not generate IDs (application layer owns identity creation):\n"
        + "\n".join(violations)
    )


def test_domain_events_are_primitives() -> None:
    """Domain events must extend DomainEvent primitive."""
    violations: list[str] = []
    event_files = list(MODULES.glob("*/domain/**/*event*.py"))

    for filepath in event_files:
        try:
            content = filepath.read_text()
            tree = ast.parse(content)
            classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}

            for node in classes.values():
                if not _class_defines_event_contract(node):
                    continue
                if not _inherits_domain_event(node, classes, set()):
                    violations.append(f"{filepath}:{node.name} does not inherit DomainEvent")
        except Exception:
            logger.debug(
                "Skipping %s: failed to parse for domain-event scan.", filepath, exc_info=True
            )

    assert not violations, "Domain events must inherit DomainEvent:\n" + "\n".join(violations)


def test_application_layer_never_imports_concrete_adapters() -> None:
    """Application code depends on ports; concrete adapters are wired in app/bootstrap.

    ``providers.py`` is the per-context registration seam and may name adapters.
    """
    violations: list[str] = []
    for filepath in MODULES.glob("*/application/**/*.py"):
        if filepath.name == "providers.py":
            continue
        for imp in _get_imports(filepath):
            if imp.startswith("app.modules.") and ".infrastructure" in imp:
                violations.append(f"{filepath}: imports {imp}")
            if imp.startswith("app.modules.") and ".api." in imp:
                violations.append(f"{filepath}: imports {imp}")

    assert not violations, "Application layer imports infrastructure/API:\n" + "\n".join(violations)


def _class_defines_event_contract(node: ast.ClassDef) -> bool:
    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        for target in item.targets:
            if isinstance(target, ast.Name) and target.id == "EVENT_TYPE":
                return True
    return False


def _inherits_domain_event(
    node: ast.ClassDef,
    classes: dict[str, ast.ClassDef],
    seen: set[str],
) -> bool:
    if node.name in seen:
        return False
    seen.add(node.name)

    for base in node.bases:
        base_name = _base_name(base)
        if base_name == "DomainEvent":
            return True
        parent = classes.get(base_name)
        if parent is not None and _inherits_domain_event(parent, classes, seen):
            return True
    return False


def _base_name(expr: ast.expr) -> str:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return ast.unparse(expr).split(".")[-1]

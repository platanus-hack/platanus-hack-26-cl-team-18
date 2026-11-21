"""
Domain pattern guardrails.

Mechanically enforces the architecture patterns that keep domain code
correct and replayable:

- Policies and Specifications stay pure (rules.md §4.4, §4.10)
- Snapshots are frozen value objects (rules.md §4.9)
- Append-only tables are never deleted from, and immutable-row tables are
  only updated through annotated write-once markers (rules.md §4.9)

Each check lands at zero violations. Do not add allowlists — fix the code or,
for the append-only check, annotate the deliberate exception inline with
`# append-only-exempt: <reason>`.

The detectors take a root so `test_detectors_catch_their_violations` can run
them against fixtures: a check that never fires is worse than no check.
"""

from __future__ import annotations

import ast
import re
from collections import deque
from pathlib import Path

APP = Path(__file__).resolve().parents[3] / "app"

# Collaborators that make a Policy or Specification impure: they perform IO,
# so a policy holding one can no longer be evaluated deterministically.
IO_COLLABORATOR_NAMES = {
    "repo",
    "repository",
    "uow",
    "unit_of_work",
    "session",
    "db",
    "conn",
    "connection",
    "engine",
    "client",
    "gateway",
    "bus",
    "publisher",
    "http",
    "httpx",
}
IO_COLLABORATOR_SUFFIXES = (
    "_repo",
    "_repository",
    "_uow",
    "_session",
    "_client",
    "_gateway",
    "_connection",
    "_bus",
)

# Tables whose rows are history: corrections happen by appending a reversal,
# never by deleting the original row.
APPEND_ONLY_TABLES = {
    "outbox_event",
    "auction",
    "charge",
    # Add tables whose rows are history: audit log, sensor readings, ...
}

# Tables whose rows are immutable facts. The only legitimate UPDATE is a
# guarded write-once marker (a reversal stamp, a NULL -> value resolution, a
# supersession link); each must say so with `# append-only-exempt: <reason>`
# on the statement or in the lines just above it. Tables with a real lifecycle
# (`campaign`, `screen`, `outbox_event`, ...) are deliberately absent.
IMMUTABLE_ROW_TABLES: set[str] = {
    "auction",
    "charge",
    # Add tables whose rows are immutable facts, e.g. raw sensor readings.
}

# How far above a statement an `append-only-exempt:` annotation may sit: the
# comment usually precedes the `self._execute(` call that holds the SQL.
_EXEMPTION_LOOKBACK_LINES = 4

# `\s` spans newlines, so both patterns match statements split across lines.
_DELETE_STATEMENT = re.compile(r"DELETE\s+FROM\s+(?:public\.)?\"?([a-z_]+)\"?", re.IGNORECASE)
_UPDATE_STATEMENT = re.compile(
    r"\bUPDATE\s+(?:public\.)?\"?([a-z_]+)\"?\s+(?:(?:AS\s+)?[a-z_]+\s+)?SET\b", re.IGNORECASE
)


def _python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except OSError, SyntaxError:
        return None


def _class_definitions(tree: ast.Module) -> list[ast.ClassDef]:
    """Keep nested statement scopes; expressions cannot contain class definitions."""
    classes: list[ast.ClassDef] = []
    pending: deque[ast.AST] = deque([tree])
    while pending:
        node = pending.popleft()
        if isinstance(node, ast.ClassDef):
            classes.append(node)
        pending.extend(
            child for child in ast.iter_child_nodes(node) if not isinstance(child, ast.expr)
        )
    return classes


def _base_names(node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _is_io_collaborator(param_name: str) -> bool:
    normalized = param_name.lstrip("_")
    return normalized in IO_COLLABORATOR_NAMES or normalized.endswith(IO_COLLABORATOR_SUFFIXES)


def find_impure_policies(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _python_files(root):
        tree = _parse(path)
        if tree is None:
            continue
        for node in _class_definitions(tree):
            # A class is a Policy/Specification by base *or* by name: a class
            # called `*Policy` that holds a repository (or a Protocol whose
            # methods take a uow) tells readers it is pure when it is not.
            named_as_policy = node.name.endswith(("Policy", "Specification"))
            if not (_base_names(node) & {"Policy", "Specification"} or named_as_policy):
                continue
            for func in ast.walk(node):
                if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                args = func.args
                for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                    if arg.arg in {"self", "cls"} or not _is_io_collaborator(arg.arg):
                        continue
                    violations.append(
                        f"{path}:{func.lineno}: {node.name}.{func.name}({arg.arg}) "
                        f"takes an IO collaborator"
                    )
    return violations


def find_unfrozen_snapshots(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _python_files(root):
        if "domain" not in path.relative_to(root).parts:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        for node in _class_definitions(tree):
            if not node.name.endswith("Snapshot"):
                continue
            frozen = False
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name != "dataclass":
                    continue
                frozen = any(
                    kw.arg == "frozen" and isinstance(kw.value, ast.Constant) and kw.value.value
                    for kw in decorator.keywords
                )
            if not frozen:
                violations.append(f"{path}:{node.lineno}: {node.name}")
    return violations


def _find_unannotated_statements(
    root: Path, pattern: re.Pattern[str], tables: set[str], verb: str
) -> list[str]:
    violations: list[str] = []
    for path in _python_files(root):
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for match in pattern.finditer(source):
            table = match.group(1).lower()
            if table not in tables:
                continue
            first = source.count("\n", 0, match.start())
            last = source.count("\n", 0, match.end())
            context = lines[max(0, first - _EXEMPTION_LOOKBACK_LINES) : last + 1]
            if any("append-only-exempt:" in line for line in context):
                continue
            violations.append(f"{path}:{first + 1}: {verb} {table}")
    return violations


def find_append_only_deletes(root: Path, tables: set[str] | None = None) -> list[str]:
    tables = APPEND_ONLY_TABLES if tables is None else tables
    return _find_unannotated_statements(root, _DELETE_STATEMENT, tables, "DELETE FROM")


def find_immutable_row_updates(root: Path, tables: set[str] | None = None) -> list[str]:
    tables = IMMUTABLE_ROW_TABLES if tables is None else tables
    return _find_unannotated_statements(root, _UPDATE_STATEMENT, tables, "UPDATE")


def _aggregate_class_names(classes_by_path: dict[Path, list[ast.ClassDef]]) -> set[str]:
    """Names of every class that reaches ``AggregateRoot`` through inheritance.

    Resolved by name across the whole tree rather than per-file, because
    intermediate bases (``Campaign`` → a module-local base → ``AggregateRoot``)
    routinely live in a different file from the leaf aggregate.
    """
    bases_by_class: dict[str, set[str]] = {}
    for classes in classes_by_path.values():
        for node in classes:
            bases_by_class.setdefault(node.name, set()).update(_base_names(node))

    aggregates = {"AggregateRoot"}
    while True:
        grown = {name for name, bases in bases_by_class.items() if bases & aggregates} - aggregates
        if not grown:
            return aggregates - {"AggregateRoot"}
        aggregates |= grown


def _is_replace_on_self(node: ast.AST) -> bool:
    """``replace(self, ...)`` / ``dataclasses.replace(self, ...)``."""
    if not isinstance(node, ast.Call) or not node.args:
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    first = node.args[0]
    return name == "replace" and isinstance(first, ast.Name) and first.id == "self"


def find_bare_replace_in_aggregates(root: Path) -> list[str]:
    classes_by_path = {
        path: _class_definitions(tree)
        for path in _python_files(root)
        if (tree := _parse(path)) is not None
    }
    aggregates = _aggregate_class_names(classes_by_path)

    violations: list[str] = []
    for path, classes in classes_by_path.items():
        for node in classes:
            if node.name not in aggregates:
                continue
            for call in ast.walk(node):
                if _is_replace_on_self(call):
                    violations.append(f"{path}:{call.lineno}: {node.name} uses replace(self, ...)")
    return sorted(violations)


def test_policies_and_specifications_take_no_io_collaborators() -> None:
    """
    Policies and Specifications are pure functions over their inputs.

    A policy that holds a repository, a session, or a gateway can no longer be
    replayed: the same inputs stop producing the same decision. Fetch the data
    in the application layer and pass it in.

    Rule: rules.md §4.4 (Policies) and §4.10 (Specifications)
    """
    violations = find_impure_policies(APP)
    assert not violations, (
        "Policies and Specifications must stay pure — load data in the "
        "application layer and pass values in:\n" + "\n".join(violations)
    )


def test_snapshot_types_are_frozen_dataclasses() -> None:
    """
    A snapshot records what was true at a point in time; it must never drift.

    Anything named `*Snapshot` in the domain is captured state, so it is a
    frozen dataclass. A mutable snapshot silently rewrites history.

    Rule: rules.md §4.9 (Snapshots)
    """
    violations = find_unfrozen_snapshots(APP)
    assert not violations, "Snapshot types must be @dataclass(frozen=True):\n" + "\n".join(
        violations
    )


def test_append_only_tables_are_never_deleted_from() -> None:
    """
    The charge ledger and audit history are corrected by appending, never by deleting.

    Rule: rules.md §4.9 (Append-Only Ledger, Reversal Instead of Deletion)
    """
    violations = find_append_only_deletes(APP)
    assert not violations, (
        "Append-only tables must be corrected by reversal, not deletion "
        "(annotate a deliberate exception with `# append-only-exempt: "
        "<reason>`):\n" + "\n".join(violations)
    )


def test_immutable_rows_are_only_updated_through_annotated_markers() -> None:
    """
    An immutable fact row is never rewritten; at most a write-once marker lands.

    Rule: rules.md §4.9 (Append-Only Ledger, Reversal Instead of Deletion)
    """
    violations = find_immutable_row_updates(APP)
    assert not violations, (
        "Immutable-row tables accept only guarded write-once markers; annotate "
        "each with `# append-only-exempt: <reason>` or append a reversal row "
        "instead:\n" + "\n".join(violations)
    )


def test_aggregates_evolve_instead_of_calling_replace_directly() -> None:
    """
    Aggregate intent methods copy themselves with `_evolve`, never bare `replace`.

    `dataclasses.replace` resets every `init=False` field on the copy — which on
    an `AggregateRoot` means `_events` and `_persisted_version`. Two intent
    methods chained inside one command therefore silently drop the first one's
    domain events before the UoW ever drains them, and the copy loses the anchor
    that optimistic concurrency writes `WHERE version = :expected_version`
    against. `AggregateRoot._evolve` carries both across.

    Rule: rules.md §4.2 (Aggregates), §5.4 (Transactional Outbox), §5.7 (OCC)
    """
    violations = find_bare_replace_in_aggregates(APP)
    assert not violations, (
        "Aggregate intent methods must use `self._evolve(...)`, not "
        "`replace(self, ...)` — bare replace drops pending domain events and "
        "the optimistic-concurrency anchor:\n" + "\n".join(violations)
    )


def test_detectors_catch_their_violations(tmp_path: Path) -> None:
    """Each detector fires on the bad shape and stays silent on the good one."""
    policies = tmp_path / "modules" / "campaigns" / "domain"
    policies.mkdir(parents=True)
    (policies / "pacing_policy.py").write_text(
        "class PacingPolicy(Policy[Input, Output]):\n"
        "    def __init__(self, campaign_repo) -> None:\n"
        "        self._campaign_repo = campaign_repo\n"
        "\n"
        "\n"
        "class ActiveSpecification(Specification[Campaign]):\n"
        "    def is_satisfied_by(self, campaign) -> bool:\n"
        "        return campaign.is_active\n"
        "\n"
        "\n"
        "class BrandKitPolicy:\n"
        "    def __init__(self, *, brand_kit_repository) -> None:\n"
        "        self._kits = brand_kit_repository\n",
        encoding="utf-8",
    )
    (policies / "snapshots.py").write_text(
        "@dataclass(frozen=True)\n"
        "class ReserveSnapshot:\n"
        "    price: Decimal\n"
        "\n"
        "\n"
        "@dataclass\n"
        "class PricingSnapshot:\n"
        "    amount: Decimal\n",
        encoding="utf-8",
    )
    infra = tmp_path / "modules" / "delivery" / "infrastructure"
    infra.mkdir(parents=True)
    (infra / "repository.py").write_text(
        'DROP_CHARGE = "DELETE FROM charge WHERE id = :id"\n'
        'DROP_DRAFT = "DELETE FROM creative_draft WHERE campaign_id = :id"\n'
        'SPLIT = """\n    DELETE\n    FROM attention_event WHERE id = :id\n"""\n'
        'REWRITE = """\n    UPDATE audience_snapshot\n    SET quantity = :q\n"""\n'
        'PRUNE = "DELETE FROM audit_log"  # append-only-exempt: fixture\n'
        "# append-only-exempt: fixture reversal stamp\n"
        'STAMP = "UPDATE charge_ledger SET reversed_at = :at WHERE reversed_at IS NULL"\n'
        'STATUS = "UPDATE charge SET status = :s"\n',
        encoding="utf-8",
    )

    # `Campaign` reaches AggregateRoot only through a base declared in another
    # file, and only `replace(self, ...)` is a violation: rebuilding some *other*
    # object is ordinary code.
    (policies / "base.py").write_text(
        "class BaseAggregate(AggregateRoot):\n    pass\n", encoding="utf-8"
    )
    (policies / "campaign.py").write_text(
        "class Campaign(BaseAggregate):\n"
        "    def activate(self):\n"
        "        return replace(self, status=ACTIVE)\n"
        "\n"
        "    def rename(self, name):\n"
        "        return self._evolve(name=name)\n"
        "\n"
        "    def with_line(self, line):\n"
        "        return self._evolve(line=replace(line, qty=1))\n"
        "\n"
        "\n"
        "class LineBuilder:\n"
        "    def bump(self):\n"
        "        return dataclasses.replace(self, qty=self.qty + 1)\n",
        encoding="utf-8",
    )

    impure = find_impure_policies(tmp_path)
    assert len(impure) == 2, impure
    assert "PacingPolicy" in impure[0]
    assert "BrandKitPolicy.__init__(brand_kit_repository)" in impure[1]

    snapshots = find_unfrozen_snapshots(tmp_path)
    assert len(snapshots) == 1, snapshots
    assert "PricingSnapshot" in snapshots[0]

    deletes = find_append_only_deletes(tmp_path, {"audit_log", "charge", "attention_event"})
    assert len(deletes) == 2, deletes
    assert "DELETE FROM charge" in deletes[0]
    assert "repository.py:4: DELETE FROM attention_event" in deletes[1]

    updates = find_immutable_row_updates(
        tmp_path, {"audit_log", "charge_ledger", "audience_snapshot"}
    )
    assert updates == [f"{infra / 'repository.py'}:8: UPDATE audience_snapshot"], updates

    bare_replace = find_bare_replace_in_aggregates(tmp_path)
    assert len(bare_replace) == 1, bare_replace
    assert "Campaign uses replace(self, ...)" in bare_replace[0]


# Retain nested definitions and fresh reads while pruning class-free expressions.
def test_aggregate_discovery_preserves_nested_classes_and_skips_expression_payloads(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "base.py").write_text("class Intermediate(AggregateRoot): pass\n")
    source = (
        'CONFIG = ["unrelated_expression_payload", 1, 2, 3]\n'
        "def build():\n"
        "    if enabled:\n"
        "        class NestedAggregate(Intermediate):\n"
        "            def change(self):\n"
        "                return replace(self, status='active')\n"
        "    try:\n"
        "        prepare()\n"
        "    except Exception:\n"
        "        class RecoveryAggregate(Intermediate):\n"
        "            def change(self):\n"
        "                return replace(self, status='recovered')\n"
    )
    path = tmp_path / "factory.py"
    path.write_text(source)
    iter_children = ast.iter_child_nodes

    def reject_unrelated_expression_walk(node):
        if (
            isinstance(node, ast.List)
            and node.elts
            and isinstance(node.elts[0], ast.Constant)
            and node.elts[0].value == "unrelated_expression_payload"
        ):
            raise AssertionError("Class discovery must not walk expression-only payloads")
        return iter_children(node)

    monkeypatch.setattr(ast, "iter_child_nodes", reject_unrelated_expression_walk)
    violations = find_bare_replace_in_aggregates(tmp_path)
    assert len(violations) == 2
    assert any("NestedAggregate uses replace(self" in item for item in violations)
    assert any("RecoveryAggregate uses replace(self" in item for item in violations)
    path.write_text(source.replace("replace(self, ", "self._evolve("))
    assert find_bare_replace_in_aggregates(tmp_path) == []

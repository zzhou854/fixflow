import ast
from pathlib import Path


def test_online_provider_package_has_no_business_mutation_capability() -> None:
    root = Path(__file__).parents[4] / "app" / "llm" / "online"
    forbidden_import_fragments = (
        "mcp",
        "repositories",
        "application.services",
        "infrastructure.database.uow",
    )
    forbidden_calls = {
        "create_ticket",
        "book_appointment",
        "reschedule_appointment",
        "escalate_to_operator",
    }
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: list[str] = []
        calls: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
        assert not any(
            fragment in imported for fragment in forbidden_import_fragments for imported in imports
        ), path
        assert forbidden_calls.isdisjoint(calls), path

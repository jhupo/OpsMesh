import ast
import hashlib
from collections import defaultdict
from pathlib import Path

groups = defaultdict(list)
for path in Path("backend/app").rglob("*.py"):
    if "__pycache__" in path.parts:
        continue
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = ast.Module(body=node.body, type_ignores=[])
            key = hashlib.sha256(ast.dump(body, include_attributes=False).encode()).hexdigest()
            groups[key].append((node.name, str(path), node.lineno, len(node.body)))
for items in groups.values():
    if len({item[1] for item in items}) > 1 and any(item[3] >= 3 for item in items):
        print("---")
        for item in items:
            print(item)

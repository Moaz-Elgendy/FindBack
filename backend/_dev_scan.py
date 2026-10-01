"""Throwaway dev check for the two shapes a truncated edit leaves behind:

1. a function that assigns a local and never reads it (a lost `return`), and
2. a name that is read but never defined anywhere in the module (a lost
   constant, e.g. the RETRY_STATUSES set that _post_json referenced).

Delete once a linter is pinned in requirements.
"""
import ast
import builtins
import sys


def defined_names(tree):
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            names |= {a.arg for a in getattr(node.args, "args", [])} if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)) else set()
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            names |= {t.id for t in ast.walk(node.target) if isinstance(t, ast.Name)}
        elif isinstance(node, ast.Global):
            names |= set(node.names)
    return names


for path in sys.argv[1:]:
    tree = ast.parse(open(path, encoding="utf-8-sig").read(), path)
    known = defined_names(tree) | set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in known:
            print(f"{path}:{node.lineno}: undefined name {node.id!r}")
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigned, read = {}, set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                if isinstance(sub.ctx, ast.Store):
                    assigned.setdefault(sub.id, sub.lineno)
                else:
                    read.add(sub.id)
        dead = sorted(name for name in assigned if name not in read and not name.startswith("_"))
        if dead:
            print(f"{path}:{node.lineno}: {node.name} assigns-but-never-reads {dead}")

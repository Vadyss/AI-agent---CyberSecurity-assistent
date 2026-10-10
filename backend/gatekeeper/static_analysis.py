# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Conservative AST checks; candidate code is never executed here."""
from __future__ import annotations

import ast
import string
from .policy import Policy
from .types import Violation
from .names import violations_unique


def safe_format_template(node: ast.AST) -> bool:
    """A format template may not reach attributes or items: those run lookups hidden from the AST."""
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    try:
        fields = [field for _, field, spec, _ in string.Formatter().parse(node.value) if field is not None]
        specs = [spec for _, field, spec, _ in string.Formatter().parse(node.value) if spec]
    except ValueError:
        return False
    return all("." not in field and "[" not in field for field in fields) and all("{" not in spec for spec in specs)


def analyze_code(code: object, manifest: dict, policy: Policy, *, is_test: bool = False) -> list[Violation]:
    errors: list[Violation] = []
    def add(code: str, detail: str):
        errors.append(Violation(code=code, detail=detail))
    if not isinstance(code, str):
        return [Violation(code="INVALID_OUTPUT", detail="Code must be a string.")]
    maximum = policy.skills.max_test_bytes if is_test else policy.skills.max_code_bytes
    try:
        code_bytes = code.encode("utf-8")
    except UnicodeError:
        return [Violation(code="INVALID_OUTPUT", detail="Code is not valid UTF-8.")]
    if len(code_bytes) > maximum:
        add("CODE_TOO_LARGE", "Code exceeds the size limit.")
        return errors
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return [Violation(code="SYNTAX_ERROR", detail="Code has invalid Python syntax.")]
    nodes = list(ast.walk(tree))
    if len(nodes) > policy.skills.max_ast_nodes:
        add("CODE_TOO_LARGE", "Code exceeds the AST node limit.")
    parents = {child: parent for parent in nodes for child in ast.iter_child_nodes(parent)}
    allowed = set(policy.skills.allowed_imports) | ({"skill"} if is_test else set())
    raw_imports = manifest.get("imports", [])
    raw_imports = raw_imports if isinstance(raw_imports, list) else []
    declared = {v for v in raw_imports if isinstance(v, str)} | ({"skill"} if is_test else set())
    module_aliases: set[str] = set()
    re_aliases = {"re"}
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                root = mod.split(".")[0]
                invalid = mod not in allowed or (isinstance(node, ast.ImportFrom) and (node.level or any(a.name == "*" for a in node.names))) or not isinstance(parents.get(node), ast.Module)
                if invalid:
                    add("FORBIDDEN_IMPORT", f"Forbidden import: {mod or 'relative import'}.")
                elif mod not in declared:
                    add("UNDECLARED_IMPORT", f"Import is not declared in the manifest: {mod}.")
                for alias in node.names:
                    module_aliases.add(alias.asname or (alias.name if isinstance(node, ast.ImportFrom) else root))
                    if mod == "re" and isinstance(node, ast.Import):
                        re_aliases.add(alias.asname or "re")
        if isinstance(node, ast.arg) and node.arg.startswith("__"):
            add("FORBIDDEN_ATTRIBUTE", "Forbidden internal parameter name.")
        if isinstance(node, ast.alias) and node.asname and (node.asname.startswith("__") or node.asname in policy.skills.forbidden_calls):
            add("FORBIDDEN_ATTRIBUTE", "Forbidden import alias name.")
        if isinstance(node, ast.Name):
            if node.id in policy.skills.forbidden_calls:
                add("FORBIDDEN_CALL", f"Forbidden name or call: {node.id}.")
            elif node.id.startswith("__"):
                add("FORBIDDEN_ATTRIBUTE", f"Forbidden internal name: {node.id}.")
        if isinstance(node, ast.Attribute) and node.attr in policy.skills.forbidden_calls:
            if not (node.attr == "compile" and isinstance(node.value, ast.Name) and node.value.id in re_aliases):
                add("FORBIDDEN_CALL", f"Forbidden attribute call: {node.attr}.")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            add("FORBIDDEN_ATTRIBUTE", f"Forbidden internal attribute: {node.attr}.")
        if isinstance(node, ast.Attribute) and node.attr in policy.skills.forbidden_attributes:
            add("FORBIDDEN_ATTRIBUTE", f"Forbidden introspection attribute: {node.attr}.")
        if isinstance(node, ast.Attribute) and node.attr == "format" and not safe_format_template(node.value):
            add("FORBIDDEN_CALL", "str.format is allowed only on a literal template with plain fields.")
        if isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom)):
            add("FORBIDDEN_SYNTAX", f"Forbidden construct: {type(node).__name__}.")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.decorator_list:
            add("FORBIDDEN_SYNTAX", "Decorators are not allowed.")
        if isinstance(node, ast.ClassDef) and node.keywords:
            add("FORBIDDEN_SYNTAX", "Metaclasses are not allowed.")
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)) and len(node.value) > policy.skills.max_string_literal:
            add("CODE_TOO_LARGE", "String literal exceeds the limit.")
    for node in nodes:
        if isinstance(node, ast.Delete) and any(isinstance(target, ast.Name) and target.id in module_aliases for target in node.targets):
            add("FORBIDDEN_SYNTAX", "Deleting an imported name is not allowed.")
    def constant(node):
        if isinstance(node, ast.Constant):
            return True
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return all(constant(el) for el in node.elts)
        if isinstance(node, ast.Dict):
            return all(k is not None and constant(k) and constant(v) for k, v in zip(node.keys, node.values))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return constant(node.operand)
        if isinstance(node, ast.Call):
            safe = isinstance(node.func, ast.Name) and node.func.id == "frozenset"
            safe |= isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in re_aliases and node.func.attr == "compile"
            return safe and all(constant(a) for a in node.args) and all(constant(k.value) for k in node.keywords)
        return False
    for node in nodes:
        if isinstance(node, ast.FunctionDef):
            defaults = node.args.defaults + [v for v in node.args.kw_defaults if v is not None]
            if any(not constant(v) for v in defaults):
                add("FORBIDDEN_SYNTAX", "Function defaults must be constants.")
            annotations = [a.annotation for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs if a.annotation is not None]
            if node.returns is not None:
                annotations.append(node.returns)
            if any(any(isinstance(n, ast.Call) for n in ast.walk(a)) for a in annotations):
                add("FORBIDDEN_SYNTAX", "Annotations must not execute calls.")
        if isinstance(node, ast.ClassDef):
            if any(any(isinstance(n, ast.Call) for n in ast.walk(base)) for base in node.bases):
                add("FORBIDDEN_SYNTAX", "Class bases must not execute calls.")
            for statement in node.body:
                allowed_statement = isinstance(statement, (ast.FunctionDef, ast.ClassDef, ast.Pass))
                allowed_statement |= isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) and isinstance(statement.value.value, str)
                allowed_statement |= isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None and constant(statement.value)
                if not allowed_statement:
                    add("FORBIDDEN_SYNTAX", "Statement is not allowed in a class body.")
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and constant(node.value):
            continue
        add("FORBIDDEN_SYNTAX", "Statement is not allowed at module level.")
    if not is_test:
        entries = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
        if len(entries) != 1 or len(entries[0].args.posonlyargs + entries[0].args.args) != 2 or [a.arg for a in entries[0].args.posonlyargs + entries[0].args.args] != ["inputs", "params"] or entries[0].args.vararg or entries[0].args.kwarg or entries[0].args.kwonlyargs or entries[0].args.defaults:
            add("MISSING_ENTRYPOINT", "Exactly one run(inputs, params) function with two parameters is required.")
    return violations_unique(errors)

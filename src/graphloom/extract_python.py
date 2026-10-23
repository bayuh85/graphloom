"""Python extractor. Uses the stdlib parser, never an execution of the file."""

from __future__ import annotations

import ast

from graphloom.model import Binding, Evidence, Graph, Node, RawRef
from graphloom.util import (
    absolute_import,
    dotted_module,
    excerpt_at,
    looks_like_http_method,
    norm_http_path,
)

_LAYER = "code"


def extract_python(
    graph: Graph,
    refs: list[RawRef],
    bindings: list[Binding],
    rel: str,
    source: str,
) -> None:
    if rel.endswith("__init__.py") and not source.strip():
        return
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        warnings = graph.meta.setdefault("warnings", [])
        warnings.append(f"{rel}: Python syntax error: {exc.msg}")
        return
    lines = source.splitlines()
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            setattr(child, "parent", parent)  # noqa: B010 — parent pointer for subscripts
    dotted = dotted_module(rel)
    mod_id = f"mod:{rel}"
    graph.add_node(
        Node(
            id=mod_id,
            kind="module",
            name=dotted,
            layer=_LAYER,
            language="python",
            file=rel,
            line=1,
            end_line=max(1, len(lines)),
            detail={"dotted": dotted, "role": "module"},
        )
    )
    visitor = _Visitor(graph, refs, bindings, rel, lines, mod_id)
    visitor.visit(tree)


class _Visitor(ast.NodeVisitor):
    def __init__(
        self,
        graph: Graph,
        refs: list[RawRef],
        bindings: list[Binding],
        rel: str,
        lines: list[str],
        mod_id: str,
    ) -> None:
        self.graph = graph
        self.refs = refs
        self.bindings = bindings
        self.rel = rel
        self.lines = lines
        self.mod_id = mod_id
        self.class_names: list[str] = []
        self.class_ids: list[str] = []
        self.fn_names: list[str] = []
        self.fn_ids: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            target = alias.name if alias.asname else alias.name.split(".")[0]
            self._bind(node, local=local, imported=None, from_module=target)
        # Import statements do not nest interesting calls.

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        dotted = absolute_import(self.rel, node.level or 0, node.module)
        for alias in node.names:
            if alias.name == "*":
                self._bind(node, local="*", imported=None, from_module=dotted)
                continue
            local = alias.asname or alias.name
            self._bind(node, local=local, imported=alias.name, from_module=dotted)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qual = ".".join([*self.class_names, node.name])
        cid = f"cls:{self.rel}:{qual}"
        self.graph.add_node(
            Node(
                id=cid,
                kind="class",
                name=node.name,
                layer=_LAYER,
                language="python",
                file=self.rel,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                detail={"qualname": qual, "role": "class"},
            )
        )
        self._contains(node.lineno, cid)
        self.class_names.append(node.name)
        self.class_ids.append(cid)
        for child in node.body:
            self.visit(child)
        self.class_ids.pop()
        self.class_names.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        source = self._caller()
        if isinstance(func, ast.Name):
            self._ref("call", source, func.id, node.lineno, receiver=None)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            self._ref(
                "attrcall",
                source,
                func.attr,
                node.lineno,
                receiver=func.value.id,
            )
        elif isinstance(func, ast.Attribute):
            self._ref("attrcall", source, func.attr, node.lineno, receiver=None)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        path = _config_path(node)
        parent = getattr(node, "parent", None)
        if path and not isinstance(parent, ast.Subscript):
            self._ref("config_path", self._caller(), path, node.lineno, receiver=None)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            text = node.value.strip()
            if 3 <= len(text) <= 4000:
                self._ref("string", self._caller(), text, getattr(node, "lineno", 1), receiver=None)
        self.generic_visit(node)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qual = ".".join([*self.class_names, *self.fn_names, node.name])
        fid = f"fn:{self.rel}:{qual}"
        detail: dict = {"qualname": qual, "signature": _signature(node)}
        if self.class_ids and not self.fn_ids:
            detail["owner"] = self.class_ids[-1]
        method, path = _route_from_decorators(node.decorator_list)
        if path:
            detail["http_path"] = path
        if method:
            detail["http_method"] = method
        self.graph.add_node(
            Node(
                id=fid,
                kind="function",
                name=node.name,
                layer=_LAYER,
                language="python",
                file=self.rel,
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                detail=detail,
            )
        )
        self._contains(node.lineno, fid)
        self.fn_ids.append(fid)
        self.fn_names.append(node.name)
        for child in node.body:
            self.visit(child)
        self.fn_names.pop()
        self.fn_ids.pop()

    def _caller(self) -> str:
        if self.fn_ids:
            return self.fn_ids[-1]
        return self.mod_id

    def _scopes(self) -> tuple[str, ...]:
        scopes = [*self.fn_ids]
        scopes.append(self.mod_id)
        # Innermost first.
        return tuple(reversed(scopes))

    def _contains(self, line: int, child_id: str) -> None:
        if self.fn_ids:
            parent = self.fn_ids[-1]
        elif self.class_ids:
            parent = self.class_ids[-1]
        else:
            parent = self.mod_id
        self.graph.add_edge(
            parent,
            child_id,
            "contains",
            "parsed",
            [Evidence(self.rel, line, excerpt_at(self.lines, line))],
        )

    def _bind(
        self,
        node: ast.AST,
        local: str,
        imported: str | None,
        from_module: str | None,
    ) -> None:
        line = getattr(node, "lineno", 1)
        scope = self.fn_ids[-1] if self.fn_ids else self.mod_id
        self.bindings.append(
            Binding(
                scope_id=scope,
                module_id=self.mod_id,
                local=local,
                imported=imported,
                from_module=from_module,
                file=self.rel,
                line=line,
                excerpt=excerpt_at(self.lines, line),
            )
        )

    def _ref(self, kind: str, source: str, name: str, line: int, receiver: str | None) -> None:
        self.refs.append(
            RawRef(
                kind=kind,
                source=source,
                name=name,
                file=self.rel,
                line=line or 1,
                excerpt=excerpt_at(self.lines, line or 1),
                receiver=receiver,
                module=self.mod_id,
                scopes=self._scopes(),
            )
        )


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    parts: list[str] = []
    for arg in [*node.args.posonlyargs, *node.args.args]:
        parts.append(arg.arg)
    if node.args.vararg:
        parts.append("*" + node.args.vararg.arg)
    for arg in node.args.kwonlyargs:
        parts.append(arg.arg)
    if node.args.kwarg:
        parts.append("**" + node.args.kwarg.arg)
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}{node.name}({', '.join(parts)})"


def _route_from_decorators(decorators: list[ast.expr]) -> tuple[str | None, str | None]:
    for dec in decorators:
        found = _route_from_call(dec)
        if found[1]:
            return found
    return None, None


def _route_from_call(dec: ast.expr) -> tuple[str | None, str | None]:
    if not isinstance(dec, ast.Call):
        return None, None
    func = dec.func
    attr = func.attr.lower() if isinstance(func, ast.Attribute) else None
    fname = func.id if isinstance(func, ast.Name) else None
    strings = [
        arg.value
        for arg in dec.args
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
    ]
    method: str | None = None
    path: str | None = None
    if attr in {"get", "post", "put", "patch", "delete", "head", "options"}:
        method = attr.upper()
        if strings:
            path = strings[0]
    elif attr == "route" or fname in {"route", "router"}:
        if len(strings) >= 2 and looks_like_http_method(strings[0]):
            method = strings[0].upper()
            path = strings[1]
        elif strings:
            path = strings[0]
    for keyword in dec.keywords:
        if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
            for elt in keyword.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    method = elt.value.upper()
                    break
        if keyword.arg in {"path", "url", "route"} and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, str):
                path = keyword.value.value
    if path and path.startswith("/"):
        return method, norm_http_path(path)
    return None, None


def _config_path(node: ast.Subscript) -> str | None:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Subscript):
        slice_node = current.slice
        if isinstance(slice_node, ast.Constant) and isinstance(slice_node.value, str):
            parts.append(slice_node.value)
        else:
            return None
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    if len(parts) >= 2 or (len(parts) == 1 and "." in parts[0]):
        return ".".join(reversed(parts))
    return None

"""Resolve extractor facts into edges with an explicit provenance tag.

`parsed` means both ends were named by the syntax (an import binding, a
same-file definition, a foreign key, a literal config path). `linked` means a
later heuristic chose the endpoint (a unique function name, a table name
inside a string, a backtick, a fetch URL).
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from graphloom.model import Binding, Evidence, Graph, Node, RawRef
from graphloom.util import norm_http_path

# Names that are too common to attach by a unique-name guess. An import
# binding or a same-file definition can still connect them.
_NO_GUESS = {
    "abs",
    "all",
    "any",
    "append",
    "bool",
    "bytes",
    "callable",
    "chr",
    "clear",
    "close",
    "compile",
    "copy",
    "count",
    "decode",
    "dict",
    "dir",
    "discard",
    "dumps",
    "encode",
    "endswith",
    "enumerate",
    "error",
    "eval",
    "exec",
    "exists",
    "extend",
    "filter",
    "find",
    "findall",
    "float",
    "format",
    "get",
    "getattr",
    "group",
    "hasattr",
    "hash",
    "hex",
    "id",
    "index",
    "info",
    "input",
    "insert",
    "int",
    "isinstance",
    "issubset",
    "items",
    "iter",
    "join",
    "json",
    "keys",
    "len",
    "list",
    "load",
    "loads",
    "locals",
    "lower",
    "lstrip",
    "main",
    "map",
    "match",
    "max",
    "min",
    "mkdir",
    "next",
    "object",
    "open",
    "ord",
    "pop",
    "pow",
    "print",
    "range",
    "read",
    "readline",
    "readlines",
    "remove",
    "replace",
    "repr",
    "resolve",
    "reversed",
    "round",
    "rstrip",
    "search",
    "setdefault",
    "setattr",
    "set",
    "sort",
    "sorted",
    "split",
    "startswith",
    "str",
    "strip",
    "sub",
    "sum",
    "super",
    "tuple",
    "type",
    "update",
    "upper",
    "values",
    "warning",
    "write",
    "zip",
}

_MENTION_KINDS = {"function", "class", "module", "table", "config", "sqlscript", "document"}
_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")


def link_all(graph: Graph, refs: list[RawRef], bindings: list[Binding]) -> None:
    modules = {
        node.detail["dotted"]: node.id
        for node in graph.nodes.values()
        if node.kind == "module" and node.detail.get("dotted")
    }
    _resolve_python_bindings(graph, bindings, modules)
    _resolve_js_imports(graph, refs, bindings)
    _resolve_calls(graph, refs, bindings)
    _resolve_tables_and_config_strings(graph, refs)
    _resolve_mentions(graph, refs)
    _resolve_fetches(graph, refs)
    _resolve_foreign_keys(graph, refs)


def _resolve_python_bindings(
    graph: Graph,
    bindings: list[Binding],
    modules: dict[str, str],
) -> None:
    for binding in bindings:
        if binding.resolved_id:
            target = binding.resolved_id
        elif binding.local == "*":
            target = modules.get(binding.from_module or "")
            binding.resolved_id = None
        elif binding.imported:
            target = _resolve_python_symbol(graph, modules, binding.from_module, binding.imported)
            binding.resolved_id = target
        else:
            target = modules.get(binding.from_module or "")
            binding.resolved_id = target
        if target and binding.module_id in graph.nodes:
            graph.add_edge(
                binding.module_id,
                target,
                "imports",
                "parsed",
                [Evidence(binding.file, binding.line, binding.excerpt)],
            )


def _resolve_python_symbol(
    graph: Graph,
    modules: dict[str, str],
    dotted: str | None,
    imported: str,
) -> str | None:
    if dotted:
        submodule = f"{dotted}.{imported}"
        if submodule in modules:
            return modules[submodule]
    host = modules.get(dotted or "")
    if host is None:
        return None
    rel = graph.nodes[host].file
    for prefix in ("fn", "cls"):
        candidate = f"{prefix}:{rel}:{imported}"
        if candidate in graph.nodes:
            return candidate
    return None


def _resolve_js_imports(graph: Graph, refs: list[RawRef], bindings: list[Binding]) -> None:
    root = Path(graph.meta.get("root") or ".")
    for ref in refs:
        if ref.kind != "js_import":
            continue
        spec = ref.module or ""
        target_file = _resolve_spec(root, ref.file, spec)
        if not target_file:
            continue
        module_id = f"mod:{target_file}"
        if module_id not in graph.nodes:
            continue
        imported = ref.note or ""
        symbol_id = _js_symbol(graph, target_file, imported)
        target = symbol_id or module_id
        graph.add_edge(
            ref.source,
            target,
            "imports",
            "parsed",
            [Evidence(ref.file, ref.line, ref.excerpt)],
        )
        if ref.name and symbol_id:
            bindings.append(
                Binding(
                    scope_id=ref.source,
                    module_id=ref.source,
                    local=ref.name,
                    imported=imported,
                    from_module=None,
                    file=ref.file,
                    line=ref.line,
                    excerpt=ref.excerpt,
                    resolved_id=symbol_id,
                )
            )
        elif ref.name and imported in {"*", "default"}:
            bindings.append(
                Binding(
                    scope_id=ref.source,
                    module_id=ref.source,
                    local=ref.name,
                    imported=None,
                    from_module=None,
                    file=ref.file,
                    line=ref.line,
                    excerpt=ref.excerpt,
                    resolved_id=module_id,
                )
            )


def _js_symbol(graph: Graph, file: str, imported: str) -> str | None:
    if not imported or imported in {"*", "default", ""}:
        return None
    direct = f"fn:{file}:{imported}"
    if direct in graph.nodes:
        return direct
    class_id = f"cls:{file}:{imported}"
    if class_id in graph.nodes:
        return class_id
    for node in graph.nodes.values():
        if node.file == file and node.kind in {"function", "class"} and node.name == imported:
            return node.id
    return None


def _resolve_spec(root: Path, importer: str, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = (root / importer).parent / spec
    candidates = [base]
    if base.suffix.lower() not in _JS_EXTS:
        candidates.extend(base.with_suffix(ext) for ext in _JS_EXTS)
        candidates.extend(base / f"index{ext}" for ext in _JS_EXTS)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            continue
    return None


def _resolve_calls(graph: Graph, refs: list[RawRef], bindings: list[Binding]) -> None:
    by_scope: dict[str, list[Binding]] = defaultdict(list)
    for binding in bindings:
        if binding.resolved_id and binding.local and binding.local != "*":
            by_scope[binding.scope_id].append(binding)
    short: dict[str, list[Node]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind in {"function", "class"}:
            short[node.name].append(node)
    for ref in refs:
        if ref.kind not in {"call", "attrcall"}:
            continue
        if ref.source not in graph.nodes:
            continue
        target, provenance = _resolve_call(graph, ref, by_scope, short)
        if target is None or provenance is None:
            continue
        graph.add_edge(
            ref.source,
            target,
            "invokes",
            provenance,
            [Evidence(ref.file, ref.line, ref.excerpt)],
        )


def _resolve_call(
    graph: Graph,
    ref: RawRef,
    by_scope: dict[str, list[Binding]],
    short: dict[str, list[Node]],
) -> tuple[str | None, str | None]:
    source = graph.nodes[ref.source]
    local_map = _scope_map(by_scope, ref.scopes)
    if ref.kind == "call":
        bound = local_map.get(ref.name)
        if bound:
            return bound, "parsed"
        same = _same_file_callable(graph, source.file, ref.name)
        if same is not None:
            return same.id, "parsed"
        return _guess(ref.name, short)
    return _resolve_attr(graph, source, ref, local_map, short)


def _resolve_attr(
    graph: Graph,
    source: Node,
    ref: RawRef,
    local_map: dict[str, str],
    short: dict[str, list[Node]],
) -> tuple[str | None, str | None]:
    receiver = ref.receiver
    if receiver in {"self", "cls"}:
        method = _method_on(graph, source.detail.get("owner"), ref.name)
        if method is not None:
            return method.id, "parsed"
        return None, None
    if receiver:
        bound = local_map.get(receiver)
        if bound and bound in graph.nodes:
            member = _member(graph, graph.nodes[bound], ref.name)
            if member is not None:
                return member.id, "parsed"
        same_class = graph.nodes.get(f"cls:{source.file}:{receiver}")
        if same_class is not None:
            method = _method_on(graph, same_class.id, ref.name)
            if method is not None:
                return method.id, "parsed"
    return _guess(ref.name, short)


def _guess(name: str, short: dict[str, list[Node]]) -> tuple[str | None, str | None]:
    if len(name) < 4 or name in _NO_GUESS or (name.startswith("__") and name.endswith("__")):
        return None, None
    found = short.get(name) or []
    if len(found) == 1:
        return found[0].id, "linked"
    return None, None


def _scope_map(by_scope: dict[str, list[Binding]], scopes: tuple[str, ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for scope in reversed(scopes):
        for binding in by_scope.get(scope, []):
            if binding.resolved_id:
                found[binding.local] = binding.resolved_id
    return found


def _same_file_callable(graph: Graph, file: str, name: str) -> Node | None:
    for prefix in ("fn", "cls"):
        node = graph.nodes.get(f"{prefix}:{file}:{name}")
        if node is not None:
            return node
    return None


def _method_on(graph: Graph, class_id: str | None, name: str) -> Node | None:
    if not class_id or class_id not in graph.nodes:
        return None
    owner = graph.nodes[class_id]
    qual = owner.detail.get("qualname") or owner.name
    return graph.nodes.get(f"fn:{owner.file}:{qual}.{name}")


def _member(graph: Graph, node: Node, name: str) -> Node | None:
    if node.kind == "module":
        return _same_file_callable(graph, node.file, name)
    if node.kind == "class":
        return _method_on(graph, node.id, name)
    return None


def _resolve_tables_and_config_strings(graph: Graph, refs: list[RawRef]) -> None:
    tables = [node for node in graph.nodes.values() if node.kind == "table" and len(node.name) >= 3]
    config_by_name: dict[str, list[Node]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind == "config":
            config_by_name[node.name.lower()].append(node)
    patterns = [(table, re.compile(rf"\b{re.escape(table.name)}\b", re.IGNORECASE)) for table in tables]
    for ref in refs:
        if ref.source not in graph.nodes:
            continue
        evidence = [Evidence(ref.file, ref.line, ref.excerpt)]
        if ref.kind == "config_path":
            for config in config_by_name.get(ref.name.lower(), []):
                graph.add_edge(ref.source, config.id, "reads", "parsed", evidence)
            continue
        if ref.kind != "string":
            continue
        for table, pattern in patterns:
            if pattern.search(ref.name):
                graph.add_edge(ref.source, table.id, "touches", "linked", evidence)
        if "." in ref.name:
            for config in config_by_name.get(ref.name.lower(), []):
                graph.add_edge(ref.source, config.id, "reads", "linked", evidence)


def _resolve_mentions(graph: Graph, refs: list[RawRef]) -> None:
    by_name: dict[str, list[Node]] = defaultdict(list)
    by_qual: dict[str, list[Node]] = defaultdict(list)
    by_file: dict[str, list[Node]] = defaultdict(list)
    for node in graph.nodes.values():
        if node.kind not in _MENTION_KINDS:
            continue
        by_name[node.name.lower()].append(node)
        qual = node.detail.get("qualname")
        if isinstance(qual, str) and qual:
            by_qual[qual.lower()].append(node)
        if node.kind in {"module", "sqlscript", "document"} and node.file:
            by_file[node.file.lower()].append(node)
    for ref in refs:
        if ref.kind != "mention" or ref.source not in graph.nodes:
            continue
        targets = _mention_targets(graph, ref.name, by_name, by_qual, by_file)
        evidence = [Evidence(ref.file, ref.line, ref.excerpt)]
        for target in targets:
            if target.id == ref.source:
                continue
            graph.add_edge(ref.source, target.id, "mentions", "linked", evidence)


def _mention_targets(
    graph: Graph,
    token: str,
    by_name: dict[str, list[Node]],
    by_qual: dict[str, list[Node]],
    by_file: dict[str, list[Node]],
) -> list[Node]:
    if token in graph.nodes and graph.nodes[token].kind in _MENTION_KINDS:
        return [graph.nodes[token]]
    key = token.lower()
    hits: list[Node] = []
    seen: set[str] = set()
    for bucket in (by_name, by_qual, by_file):
        for node in bucket.get(key, []):
            if node.id in seen:
                continue
            seen.add(node.id)
            hits.append(node)
    if not hits or len(hits) > 8:
        return []
    return hits


def _resolve_fetches(graph: Graph, refs: list[RawRef]) -> None:
    by_path: dict[str, list[Node]] = defaultdict(list)
    for node in graph.nodes.values():
        path = node.detail.get("http_path")
        if node.kind == "function" and isinstance(path, str) and path:
            by_path[norm_http_path(path)].append(node)
    for ref in refs:
        if ref.kind != "fetch" or ref.source not in graph.nodes:
            continue
        path = norm_http_path(ref.name.split("?", 1)[0].strip())
        if not path.startswith("/"):
            continue
        handlers = by_path.get(path) or []
        method = (ref.note or "").upper()
        if method:
            matched = [node for node in handlers if node.detail.get("http_method") == method]
            if matched:
                handlers = matched
        evidence = [Evidence(ref.file, ref.line, ref.excerpt)]
        for handler in handlers:
            graph.add_edge(ref.source, handler.id, "hits", "linked", evidence)


def _resolve_foreign_keys(graph: Graph, refs: list[RawRef]) -> None:
    for ref in refs:
        if ref.kind != "fk" or ref.source not in graph.nodes:
            continue
        target_id = f"tbl:{ref.name.lower()}"
        if target_id not in graph.nodes:
            graph.add_node(
                Node(
                    id=target_id,
                    kind="table",
                    name=ref.name,
                    layer="data",
                    language="sql",
                    file=ref.file,
                    line=ref.line,
                    end_line=ref.line,
                    detail={"role": "table", "stub": True, "columns": []},
                )
            )
        graph.add_edge(
            ref.source,
            target_id,
            "ties",
            "parsed",
            [Evidence(ref.file, ref.line, ref.excerpt)],
        )

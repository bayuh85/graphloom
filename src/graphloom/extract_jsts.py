"""Lightweight JavaScript and TypeScript extractor.

The pass is a line scanner with brace depth, not a full parser. It records
imports, classes, functions, call names, and fetch() URLs.
"""

from __future__ import annotations

import re

from graphloom.model import Evidence, Graph, Node, RawRef
from graphloom.util import excerpt_at

_LAYER = "code"
_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "function",
    "return",
    "import",
    "export",
    "new",
    "typeof",
    "await",
    "do",
    "else",
    "try",
    "class",
    "case",
    "void",
    "delete",
    "in",
    "of",
    "instanceof",
    "super",
    "yield",
    "throw",
    "debugger",
    "with",
    "const",
    "let",
    "var",
    "async",
}

_FUNC = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("
)
_ARROW = re.compile(
    r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
_CLASS = re.compile(r"^(?:export\s+)?class\s+([A-Za-z_$][\w$]*)\b")
_METHOD = re.compile(r"^(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^;]*\)\s*\{?\s*$")
_NAMED_IMPORT = re.compile(
    r"import\s+(?:type\s+)?\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]"
)
_NAMESPACE_IMPORT = re.compile(
    r"import\s+\*\s+as\s+([A-Za-z_$][\w$]*)\s+from\s+['\"]([^'\"]+)['\"]"
)
_DEFAULT_IMPORT = re.compile(
    r"import\s+([A-Za-z_$][\w$]*)\s+from\s+['\"]([^'\"]+)['\"]"
)
_SIDE_IMPORT = re.compile(r"import\s+['\"]([^'\"]+)['\"]")
_REQUIRE = re.compile(
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\(\s*['\"]([^'\"]+)['\"]\s*\)"
)
_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
_ATTR_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)\s*\(")
_FETCH = re.compile(r"fetch\(\s*['\"]([^'\"]+)['\"]")
_FETCH_METHOD = re.compile(r"method\s*:\s*['\"]([A-Za-z]+)['\"]")
_STRING_RE = re.compile(r"('([^'\\]|\\.)*'|\"([^\"\\]|\\.)*\"|`([^`\\]|\\.)*`)")


def extract_jsts(graph: Graph, refs: list[RawRef], rel: str, source: str) -> None:
    lines = source.splitlines()
    language = "typescript" if rel.endswith((".ts", ".tsx")) else "javascript"
    mod_id = f"mod:{rel}"
    graph.add_node(
        Node(
            id=mod_id,
            kind="module",
            name=rel,
            layer=_LAYER,
            language=language,
            file=rel,
            line=1,
            end_line=max(1, len(lines)),
            detail={"role": "module"},
        )
    )
    _collect_imports(graph, refs, rel, lines, mod_id)
    _collect_symbols(graph, refs, rel, lines, mod_id, language)


def _collect_imports(
    graph: Graph,
    refs: list[RawRef],
    rel: str,
    lines: list[str],
    mod_id: str,
) -> None:
    buffer = ""
    start = 1
    statements: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        if buffer:
            buffer += " " + line.strip()
            if " from " in line or "require(" in line:
                statements.append((start, buffer))
                buffer = ""
            continue
        stripped = line.strip()
        if re.search(r"\bimport\s+(?:type\s+)?\{", stripped) and " from " not in stripped:
            buffer = stripped
            start = index
            continue
        if "import " in stripped or "require(" in stripped:
            statements.append((index, stripped))
    if buffer:
        statements.append((start, buffer))
    for line_no, text in statements:
        _emit_import(refs, rel, mod_id, line_no, text, excerpt_at(lines, line_no))
    del graph  # imports are resolved later, once every module exists


def _emit_import(
    refs: list[RawRef],
    rel: str,
    mod_id: str,
    line_no: int,
    text: str,
    excerpt: str,
) -> None:
    namespace = _NAMESPACE_IMPORT.search(text)
    if namespace:
        refs.append(
            _import_ref(mod_id, namespace.group(1), namespace.group(2), rel, line_no, excerpt, "*")
        )
        return
    named = _NAMED_IMPORT.search(text)
    if named:
        spec = named.group(2)
        for piece in named.group(1).split(","):
            piece = piece.strip()
            if not piece:
                continue
            parts = re.split(r"\s+as\s+", piece.strip())
            imported = parts[0].strip()
            local = parts[-1].strip()
            if not re.match(r"[A-Za-z_$][\w$]*$", imported):
                continue
            if not re.match(r"[A-Za-z_$][\w$]*$", local):
                continue
            refs.append(_import_ref(mod_id, local, spec, rel, line_no, excerpt, imported))
        return
    default = _DEFAULT_IMPORT.search(text)
    if default:
        refs.append(
            _import_ref(mod_id, default.group(1), default.group(2), rel, line_no, excerpt, "default")
        )
        return
    side = _SIDE_IMPORT.search(text)
    if side:
        refs.append(_import_ref(mod_id, "", side.group(1), rel, line_no, excerpt, ""))
        return
    required = _REQUIRE.search(text)
    if required:
        refs.append(
            _import_ref(
                mod_id, required.group(1), required.group(2), rel, line_no, excerpt, "*"
            )
        )


def _import_ref(
    mod_id: str,
    local: str,
    spec: str,
    rel: str,
    line_no: int,
    excerpt: str,
    imported: str,
) -> RawRef:
    return RawRef(
        kind="js_import",
        source=mod_id,
        name=local,
        file=rel,
        line=line_no,
        excerpt=excerpt,
        module=spec,
        note=imported,
        scopes=(mod_id,),
    )


def _collect_symbols(
    graph: Graph,
    refs: list[RawRef],
    rel: str,
    lines: list[str],
    mod_id: str,
    language: str,
) -> None:
    depth = 0
    stack: list[dict] = []
    for index, line in enumerate(lines):
        line_no = index + 1
        plain = _STRING_RE.sub('""', line)
        stripped = plain.strip()
        in_class = _current_class(stack) is not None and _current_fn(stack) is None
        header = _header(stripped, allow_method=in_class)
        active_before = _current_fn(stack)
        if header and header[0] == "class" and _current_fn(stack) is None:
            cid = f"cls:{rel}:{header[1]}"
            graph.add_node(
                Node(
                    id=cid,
                    kind="class",
                    name=header[1],
                    layer=_LAYER,
                    language=language,
                    file=rel,
                    line=line_no,
                    end_line=line_no,
                    detail={"qualname": header[1], "exported": header[2]},
                )
            )
            graph.add_edge(
                mod_id,
                cid,
                "contains",
                "parsed",
                [Evidence(rel, line_no, excerpt_at(lines, line_no))],
            )
            stack.append({"kind": "class", "id": cid, "brace": depth, "seen": False, "line": line_no})
        elif header and header[0] == "fn":
            owner = _current_class(stack)
            if owner:
                qual = f"{_class_name(graph, owner['id'])}.{header[1]}"
            else:
                qual = header[1]
            fid = f"fn:{rel}:{qual}"
            detail = {
                "qualname": qual,
                "signature": stripped[:160],
                "exported": header[2],
            }
            if owner:
                detail["owner"] = owner["id"]
            graph.add_node(
                Node(
                    id=fid,
                    kind="function",
                    name=header[1],
                    layer=_LAYER,
                    language=language,
                    file=rel,
                    line=line_no,
                    end_line=line_no,
                    detail=detail,
                )
            )
            parent = owner["id"] if owner else mod_id
            graph.add_edge(
                parent,
                fid,
                "contains",
                "parsed",
                [Evidence(rel, line_no, excerpt_at(lines, line_no))],
            )
            immediate = "=>" in stripped and "{" not in stripped.split("=>", 1)[-1]
            if immediate:
                node = graph.nodes[fid]
                node.end_line = line_no
                _calls(refs, rel, lines, line_no, plain, fid, mod_id, source_text=source_window(lines, index))
            else:
                stack.append(
                    {"kind": "fn", "id": fid, "brace": depth, "seen": False, "line": line_no}
                )
        caller = _current_fn(stack) or active_before
        if caller and caller["kind"] == "fn":
            _calls(
                refs,
                rel,
                lines,
                line_no,
                plain,
                caller["id"],
                mod_id,
                source_text=source_window(lines, index),
            )
        for char in plain:
            if char == "{":
                depth += 1
                if stack and not stack[-1]["seen"]:
                    stack[-1]["seen"] = True
                    stack[-1]["brace"] = depth
            elif char == "}":
                depth = max(0, depth - 1)
                while stack and stack[-1]["seen"] and depth < stack[-1]["brace"]:
                    done = stack.pop()
                    node = graph.nodes.get(done["id"])
                    if node is not None:
                        node.end_line = line_no
    while stack:
        done = stack.pop()
        node = graph.nodes.get(done["id"])
        if node is not None and node.end_line < line_end(lines):
            node.end_line = max(node.line, len(lines))


def _header(stripped: str, allow_method: bool) -> tuple[str, str, bool] | None:
    exported = stripped.startswith("export ")
    matched = _FUNC.match(stripped) or _ARROW.match(stripped)
    if matched:
        return ("fn", matched.group(1), exported)
    matched = _CLASS.match(stripped)
    if matched:
        return ("class", matched.group(1), exported)
    if not allow_method or stripped.startswith("export "):
        return None
    method = _METHOD.match(stripped)
    if method and method.group(1) not in _KEYWORDS:
        return ("fn", method.group(1), False)
    return None


def _current_fn(stack: list[dict]) -> dict | None:
    for item in reversed(stack):
        if item["kind"] == "fn":
            return item
    return None


def _current_class(stack: list[dict]) -> dict | None:
    for item in reversed(stack):
        if item["kind"] == "class":
            return item
        if item["kind"] == "fn":
            return None
    return None


def _class_name(graph: Graph, cid: str) -> str:
    node = graph.nodes.get(cid)
    return node.name if node else "Class"


def line_end(lines: list[str]) -> int:
    return max(1, len(lines))


def source_window(lines: list[str], index: int) -> str:
    return "\n".join(lines[index : index + 8])


def _calls(
    refs: list[RawRef],
    rel: str,
    lines: list[str],
    line_no: int,
    plain: str,
    fid: str,
    mod_id: str,
    source_text: str,
) -> None:
    excerpt = excerpt_at(lines, line_no)
    seen: set[tuple[str, str | None]] = set()
    for match in _ATTR_CALL.finditer(plain):
        receiver, name = match.group(1), match.group(2)
        if name in _KEYWORDS:
            continue
        key = ("attr", name, receiver)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            RawRef(
                kind="attrcall",
                source=fid,
                name=name,
                file=rel,
                line=line_no,
                excerpt=excerpt,
                receiver=receiver,
                module=mod_id,
                scopes=(fid, mod_id),
            )
        )
    for match in _CALL.finditer(plain):
        name = match.group(1)
        if name in _KEYWORDS:
            continue
        previous = plain[: match.start()].rstrip()
        if previous.endswith("."):
            continue
        key = ("call", name, None)
        if key in seen:
            continue
        seen.add(key)
        refs.append(
            RawRef(
                kind="call",
                source=fid,
                name=name,
                file=rel,
                line=line_no,
                excerpt=excerpt,
                module=mod_id,
                scopes=(fid, mod_id),
            )
        )
    if "fetch(" in lines[line_no - 1]:
        raw = source_text
        found = _FETCH.search(raw)
        if found:
            method_match = _FETCH_METHOD.search(raw)
            method = method_match.group(1).upper() if method_match else ""
            refs.append(
                RawRef(
                    kind="fetch",
                    source=fid,
                    name=found.group(1),
                    file=rel,
                    line=line_no,
                    excerpt=excerpt,
                    module=mod_id,
                    note=method,
                    scopes=(fid, mod_id),
                )
            )

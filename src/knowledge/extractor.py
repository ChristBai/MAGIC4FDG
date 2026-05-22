"""知识提取器：基于 Clang AST 的目标库结构化信息提取。

Knowledge Agent 的核心实现。在 Docker 容器内执行 clang 命令提取 API 信息。

工作流程：
1. 调用 docker_runner.build_and_extract() 在容器内 build + clang 分析
2. 解析返回的 AST JSON、宏文本、源文件 AST
3. 从中提取函数声明、类型定义、宏常量、调用关系
4. 语义标注：从注释和命名规则推断 ownership/preconditions
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.config import ROOT
from src.infra.docker_runner import build_and_extract
from src.pipeline.state import APIEntry, KnowledgeStore


# =============================================================================
# 公开接口
# =============================================================================

def extract_knowledge(target_config: dict) -> KnowledgeStore:
    """从目标库提取结构化知识，返回完整的 KnowledgeStore。"""
    header = target_config.get("header", "")
    source_files = target_config.get("source_files", [])
    include_dirs = target_config.get("include_dirs", [])

    # 在 Docker 内执行 build + clang 分析
    raw = build_and_extract(target_config)

    # Phase 1: AST 解析
    ast_json = None
    if raw.get("ast_header"):
        try:
            ast_json = json.loads(raw["ast_header"])
        except (json.JSONDecodeError, TypeError):
            pass

    api_entries = _extract_functions(ast_json, header) if ast_json else []
    type_defs = _extract_types(ast_json, header) if ast_json else []
    comments = _extract_comments(ast_json) if ast_json else {}

    # Fallback: regex 提取
    if not api_entries:
        api_entries = _extract_functions_regex(header, include_dirs)

    # Phase 2: 宏提取（从原始文本解析）
    macros = _parse_macros_text(raw.get("macros", ""))

    # Phase 3: 调用图
    raw_graph: dict[str, list[str]] = {}
    for src_ast_text in raw.get("source_asts", []):
        if not src_ast_text:
            continue
        try:
            src_ast = json.loads(src_ast_text)
        except (json.JSONDecodeError, TypeError):
            continue
        cg = _extract_call_graph(src_ast)
        for caller, callees in cg.items():
            raw_graph.setdefault(caller, []).extend(callees)
    call_graph = {caller: sorted(set(callees)) for caller, callees in raw_graph.items()}

    # Phase 4: 语义标注
    _annotate_from_comments(api_entries, comments)
    _infer_ownership(api_entries)
    _infer_categories(api_entries)

    # Phase 5: 附加注释到 APIEntry
    for entry in api_entries:
        if entry["name"] in comments:
            entry["doc_comment"] = comments[entry["name"]]

    return KnowledgeStore(
        api_entries=api_entries,
        call_graph=call_graph,
        type_definitions=type_defs,
        macro_constants=macros,
        slot_knowledge={},
    )


# =============================================================================
# Regex fallback（AST dump 失败时）
# =============================================================================

def _extract_functions_regex(header_path: str, include_dirs: list[str]) -> list[APIEntry]:
    """Fallback：用正则从头文件提取函数声明。"""
    entries: list[APIEntry] = []
    seen: set[str] = set()

    paths_to_scan = [Path(ROOT / header_path)] if header_path else []
    for d in include_dirs:
        p = Path(ROOT / d) if not d.startswith("/") else Path(d)
        if p.is_dir():
            paths_to_scan.extend(p.glob("*.h"))

    func_pattern = re.compile(
        r"^\s*(?:extern\s+)?(?:OSSL_DEPRECATEDIN_\S+\s+)?"
        r"([\w\s\*]+?)\s+(\w+)\s*\(([^)]*)\)\s*;",
        re.MULTILINE,
    )

    for path in paths_to_scan:
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in func_pattern.finditer(content):
            ret_type = m.group(1).strip()
            name = m.group(2)
            if name in seen or name.startswith("_"):
                continue
            if any(kw in ret_type for kw in ["#", "typedef", "struct", "enum"]):
                continue
            seen.add(name)
            entries.append(APIEntry(
                name=name,
                signature=f"{ret_type} {name}({m.group(3).strip()})",
                return_type=ret_type,
                params=[],
                category="utility",
                preconditions=[],
                group="",
                doc_comment="",
            ))

    return entries


# =============================================================================
# Phase 1: 函数提取
# =============================================================================

def _extract_functions(ast: dict, header_path: str) -> list[APIEntry]:
    """从 AST 中提取函数声明。"""
    entries: list[APIEntry] = []
    header_name = Path(header_path).name if header_path else ""

    for node in _walk_ast(ast):
        if node.get("kind") != "FunctionDecl":
            continue
        loc = node.get("loc", {})
        file_field = loc.get("file", "") or loc.get("expansionLoc", {}).get("file", "")
        if file_field and header_name and header_name not in file_field:
            continue
        name = node.get("name", "")
        if not name or name.startswith("_"):
            continue
        qual_type = node.get("type", {}).get("qualType", "")
        params = _extract_params(node)
        return_type = _infer_return_type(qual_type)
        entries.append(APIEntry(
            name=name, signature=qual_type, return_type=return_type,
            params=params, category="utility", preconditions=[],
            group="", doc_comment="",
        ))

    return entries


def _extract_params(func_node: dict) -> list[dict]:
    params = []
    for child in func_node.get("inner", []):
        if child.get("kind") != "ParmVarDecl":
            continue
        ptype = child.get("type", {}).get("qualType", "")
        params.append({"name": child.get("name", ""), "type": ptype,
                       "nullable": "*" in ptype, "ownership": "borrow"})
    return params


def _infer_return_type(qual_type: str) -> str:
    paren_idx = qual_type.find("(")
    if paren_idx > 0:
        return qual_type[:paren_idx].strip()
    return qual_type


# =============================================================================
# Phase 1: 类型提取
# =============================================================================

def _extract_types(ast: dict, header_path: str) -> list[dict]:
    """提取 struct、enum、typedef 定义。"""
    types = []
    seen: set[tuple[str, str]] = set()
    header_name = Path(header_path).name if header_path else ""

    for node in _walk_ast(ast):
        kind = node.get("kind", "")
        name = node.get("name", "")
        if not name:
            continue
        loc = node.get("loc", {})
        file_field = loc.get("file", "") or loc.get("expansionLoc", {}).get("file", "")
        if file_field and header_name and header_name not in file_field:
            continue

        if kind == "RecordDecl" and (kind, name) not in seen:
            if not node.get("completeDefinition"):
                continue
            fields = [{"name": c.get("name", ""), "type": c.get("type", {}).get("qualType", "")}
                      for c in node.get("inner", []) if c.get("kind") == "FieldDecl"]
            types.append({"kind": "struct", "name": name, "fields": fields})
            seen.add((kind, name))
        elif kind == "EnumDecl" and (kind, name) not in seen:
            values = [c.get("name", "") for c in node.get("inner", []) if c.get("kind") == "EnumConstantDecl"]
            types.append({"kind": "enum", "name": name, "values": values})
            seen.add((kind, name))
        elif kind == "TypedefDecl" and (kind, name) not in seen:
            underlying = node.get("type", {}).get("qualType", "")
            types.append({"kind": "typedef", "name": name, "underlying": underlying})
            seen.add((kind, name))

    return types


# =============================================================================
# Phase 1: 注释提取
# =============================================================================

def _extract_comments(ast: dict) -> dict[str, str]:
    """提取附着在声明上的文档注释。"""
    comments: dict[str, str] = {}
    for node in _walk_ast(ast):
        kind = node.get("kind", "")
        if kind not in ("FunctionDecl", "RecordDecl"):
            continue
        name = node.get("name", "")
        if not name:
            continue
        for child in node.get("inner", []):
            if child.get("kind") == "FullComment":
                text = _collect_comment_text(child)
                if text:
                    comments[name] = text
                break
    return comments


def _collect_comment_text(comment_node: dict) -> str:
    texts = []
    if comment_node.get("kind") == "TextComment":
        t = comment_node.get("text", "").strip()
        if t:
            texts.append(t)
    for child in comment_node.get("inner", []):
        texts.append(_collect_comment_text(child))
    return " ".join(t for t in texts if t)


# =============================================================================
# Phase 2: 宏解析
# =============================================================================

def _parse_macros_text(raw_text: str) -> list[dict]:
    """解析 clang -dM -E 的原始输出为宏列表。"""
    if not raw_text:
        return []
    macros = []
    for line in raw_text.splitlines():
        m = re.match(r"#define\s+(\w+)(?:\(([^)]*)\))?\s*(.*)", line)
        if not m:
            continue
        name, params, value = m.group(1), m.group(2), m.group(3).strip()
        if name.startswith("__") or (name.startswith("_") and len(name) > 1 and name[1].isupper()):
            continue
        if name.endswith("_H") or name.endswith("_H_"):
            continue
        if params is not None:
            kind = "function_like"
        elif "<<" in value:
            kind = "flag"
        else:
            kind = "constant"
        macros.append({"name": name, "value": value, "kind": kind})
    return macros


# =============================================================================
# Phase 3: 调用图
# =============================================================================

def _extract_call_graph(ast: dict) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    _extract_calls_recursive(ast, "", graph)
    return graph


def _extract_calls_recursive(node: dict, current_func: str, graph: dict) -> None:
    kind = node.get("kind", "")
    if kind == "FunctionDecl":
        current_func = node.get("name", "")
    elif kind == "CallExpr" and current_func:
        for child in node.get("inner", []):
            callee = _resolve_callee(child)
            if callee:
                graph.setdefault(current_func, []).append(callee)
                break
    for child in node.get("inner", []):
        if child:
            _extract_calls_recursive(child, current_func, graph)


def _resolve_callee(node: dict) -> str:
    kind = node.get("kind", "")
    if kind == "DeclRefExpr":
        return node.get("referencedDecl", {}).get("name", "")
    if kind == "ImplicitCastExpr":
        for child in node.get("inner", []):
            if child.get("kind") == "DeclRefExpr":
                return child.get("referencedDecl", {}).get("name", "")
    return ""


# =============================================================================
# Phase 4: 语义标注
# =============================================================================

def _annotate_from_comments(entries: list[APIEntry], comments: dict[str, str]) -> None:
    """从文档注释中提取 ownership 和 preconditions。"""
    return_own_patterns = [
        re.compile(r"caller\s+is\s+(always\s+)?responsible\s+to\s+free", re.I),
        re.compile(r"must\s+be\s+freed\s+by\s+(the\s+)?caller", re.I),
        re.compile(r"returns?\s+a\s+new(ly)?\s+(allocated|created)", re.I),
    ]
    param_transfer_patterns = [
        re.compile(r"takes?\s+ownership", re.I),
        re.compile(r"will\s+(be\s+)?free[d]?\s+(the|this)", re.I),
    ]
    precondition_patterns = [
        re.compile(r"must\s+not\s+be\s+NULL", re.I),
        re.compile(r"must\s+be\s+(a\s+)?valid", re.I),
        re.compile(r"cannot\s+be\s+NULL", re.I),
        re.compile(r"must\s+be\s+(a\s+)?non-?null", re.I),
    ]

    for entry in entries:
        comment = comments.get(entry["name"], "")
        if not comment:
            continue
        for pattern in return_own_patterns:
            if pattern.search(comment):
                precond = "Return value must be freed by caller"
                if precond not in entry["preconditions"]:
                    entry["preconditions"].append(precond)
                break
        for pattern in param_transfer_patterns:
            if pattern.search(comment):
                for p in entry["params"]:
                    if "*" in p["type"]:
                        p["ownership"] = "transfer"
                        break
                break
        for pattern in precondition_patterns:
            match = pattern.search(comment)
            if match:
                start = max(0, comment.rfind(".", 0, match.start()) + 1)
                end = comment.find(".", match.end())
                if end < 0:
                    end = len(comment)
                precond = comment[start:end].strip()
                if precond and precond not in entry["preconditions"]:
                    entry["preconditions"].append(precond)


def _infer_ownership(entries: list[APIEntry]) -> None:
    """根据命名规则推断 ownership。"""
    create_pattern = re.compile(r"(Create|New|Duplicate|Copy|Clone|Alloc)", re.I)
    delete_pattern = re.compile(r"(Delete|Free|Destroy|Release|Close)", re.I)

    for entry in entries:
        name = entry["name"]
        if create_pattern.search(name) and "*" in entry["return_type"]:
            precond = "Return value must be freed by caller"
            if precond not in entry["preconditions"]:
                entry["preconditions"].append(precond)
        if delete_pattern.search(name):
            for p in entry["params"]:
                if "*" in p["type"] and p["ownership"] == "borrow":
                    p["ownership"] = "transfer"
                    break


def _infer_categories(entries: list[APIEntry]) -> None:
    """根据函数命名规则推断 API 分类。"""
    patterns = [
        ("parse", re.compile(r"(parse|read|decode|load|from|input|scan)", re.I)),
        ("create", re.compile(r"(create|new|alloc|init|open|make)", re.I)),
        ("delete", re.compile(r"(delete|remove|free|destroy|close|release|pop|clear)", re.I)),
        ("modify", re.compile(r"(add|set|replace|insert|append|push|put|update)", re.I)),
        ("query", re.compile(r"(get|has|is|find|lookup|search|count|size|length)", re.I)),
        ("serialize", re.compile(r"(print|write|encode|save|dump|serialize|to_string|format|output)", re.I)),
    ]
    for entry in entries:
        for category, pattern in patterns:
            if pattern.search(entry["name"]):
                entry["category"] = category
                break


# =============================================================================
# 工具函数
# =============================================================================

def _walk_ast(node: dict):
    """深度优先遍历 AST 所有节点。"""
    if not node:
        return
    yield node
    for child in node.get("inner", []):
        if child:
            yield from _walk_ast(child)

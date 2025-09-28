"""Utilities for loading and matching Freshservice taxonomy configurations."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

LOGGER = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")

_VENDOR_EXPANSIONS: Dict[str, List[str]] = {
    "ms": ["microsoft"],
    "m365": ["office 365", "microsoft 365"],
    "cc": ["creative cloud"],
    "ad": ["active directory"],
}

_SIMPLE_ALIASES: Dict[str, List[str]] = {
    "v-ray": ["vray"],
    "vray": ["v-ray"],
    "c4d": ["cinema4d", "cinema 4d"],
    "cinema4d": ["c4d", "cinema 4d"],
    "cinema 4d": ["c4d", "cinema4d"],
    "sign-in": ["signin", "sign in"],
    "sign in": ["signin", "sign-in"],
    "signin": ["sign-in", "sign in"],
}


def _normalise_priority_path(path: Any) -> Tuple[str, ...]:
    if isinstance(path, (list, tuple)):
        parts = [str(part) for part in path]
    elif isinstance(path, str):
        parts = [segment.strip() for segment in path.split(">")]
    else:  # pragma: no cover - defensive guard
        raise ValueError(f"Unsupported priority path type: {type(path)!r}")
    return tuple(part for part in parts if part)


@dataclass(frozen=True)
class AliasRule:
    alias: str
    target_path: Tuple[str, ...]
    legacy: bool = False
    note: Optional[str] = None
    regex: Optional[re.Pattern[str]] = None


@dataclass
class TaxonomyNode:
    label: str
    path: Tuple[str, ...]
    keywords: List[str] = field(default_factory=list)
    keyword_norms: List[str] = field(default_factory=list)
    regexes: List[re.Pattern[str]] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    children: List["TaxonomyNode"] = field(default_factory=list)

    def iter_nodes(self) -> Iterator["TaxonomyNode"]:
        yield self
        for child in self.children:
            yield from child.iter_nodes()


@dataclass
class TaxonomyModel:
    nodes_by_path: Dict[Tuple[str, ...], TaxonomyNode]
    priority_map: Dict[Tuple[str, ...], int]
    alias_rules: List[AliasRule]
    max_depth: int = 3

    def iter_nodes(self) -> Iterator[TaxonomyNode]:
        for node in self.nodes_by_path.values():
            # Only yield root nodes to avoid duplicates when walking children.
            if len(node.path) == 1:
                yield from node.iter_nodes()

    def get_node(self, path: Tuple[str, ...]) -> Optional[TaxonomyNode]:
        return self.nodes_by_path.get(path)


def _build_node(
    entry: Dict[str, Any],
    *,
    parent_path: Tuple[str, ...],
    max_depth: int,
    nodes_by_path: Dict[Tuple[str, ...], TaxonomyNode],
    alias_rules: List[AliasRule],
) -> TaxonomyNode:
    if "label" not in entry:
        raise ValueError("Each taxonomy node must include a 'label' field")
    label = entry["label"]
    if not isinstance(label, str) or not label.strip():
        raise ValueError("Taxonomy node labels must be non-empty strings")
    label = label.strip()
    path = parent_path + (label,)
    if len(path) > max_depth:
        raise ValueError(
            f"Taxonomy depth exceeds the supported maximum of {max_depth} levels: {' > '.join(path)}"
        )

    keywords = _derive_label_keywords(label, extras=entry.get("keywords"))
    regexes = [re.compile(pattern, re.IGNORECASE) for pattern in entry.get("regexes", [])]
    alias_list = [str(alias) for alias in entry.get("aliases", [])]

    node = TaxonomyNode(
        label=label,
        path=path,
        keywords=keywords,
        keyword_norms=[keyword.lower() for keyword in keywords],
        regexes=regexes,
        aliases=alias_list,
        children=[],
    )
    if path in nodes_by_path:
        raise ValueError(f"Duplicate taxonomy path detected: {' > '.join(path)}")
    nodes_by_path[path] = node

    for alias in alias_list:
        alias_rules.append(
            AliasRule(alias=alias, target_path=path, legacy=False, note=None, regex=None)
        )

    for child_entry in entry.get("children", []) or []:
        child_node = _build_node(
            child_entry,
            parent_path=path,
            max_depth=max_depth,
            nodes_by_path=nodes_by_path,
            alias_rules=alias_rules,
        )
        node.children.append(child_node)

    return node


def _parse_alias_rules(
    entries: Iterable[Dict[str, Any]],
    *,
    nodes_by_path: Dict[Tuple[str, ...], TaxonomyNode],
    alias_rules: List[AliasRule],
) -> None:
    for entry in entries:
        alias = str(entry.get("alias") or "").strip()
        if not alias:
            raise ValueError("Alias entries must include a non-empty 'alias' field")
        target = entry.get("target")
        if not target:
            raise ValueError(f"Alias '{alias}' is missing a target path")
        target_path = _normalise_priority_path(target)
        if target_path not in nodes_by_path:
            LOGGER.warning(
                "Alias '%s' points to unknown taxonomy path '%s'", alias, " > ".join(target_path)
            )
        legacy = bool(entry.get("legacy", False))
        note = entry.get("note")
        pattern: Optional[re.Pattern[str]] = None
        if entry.get("regex"):
            pattern = re.compile(alias, re.IGNORECASE)
        alias_rules.append(
            AliasRule(alias=alias, target_path=target_path, legacy=legacy, note=note, regex=pattern)
        )


def build_taxonomy_model(
    config: Optional[Dict[str, Any]],
    *,
    available_taxonomy: Optional[
        Tuple[List[str], Dict[str | None, List[str]], Dict[Tuple[str | None, str | None], List[str]]]
    ] = None,
    max_depth: int = 3,
) -> TaxonomyModel:
    """Construct a :class:`TaxonomyModel` from configuration data."""

    if not config:
        if not available_taxonomy:
            raise ValueError("Configuration is missing the 'taxonomy' section")
        LOGGER.info(
            "No taxonomy configuration supplied; building model from Freshservice metadata"
        )
        return _build_model_from_metadata(available_taxonomy, max_depth=max_depth)

    tree_entries = config.get("tree")
    if not isinstance(tree_entries, list) or not tree_entries:
        raise ValueError("taxonomy.tree must be a non-empty list")

    nodes_by_path: Dict[Tuple[str, ...], TaxonomyNode] = {}
    alias_rules: List[AliasRule] = []

    for entry in tree_entries:
        _build_node(
            entry,
            parent_path=(),
            max_depth=max_depth,
            nodes_by_path=nodes_by_path,
            alias_rules=alias_rules,
        )

    additional_aliases = config.get("aliases") or []
    if additional_aliases:
        _parse_alias_rules(additional_aliases, nodes_by_path=nodes_by_path, alias_rules=alias_rules)

    priority_entries = config.get("priority_order", [])
    priority_map: Dict[Tuple[str, ...], int] = {}
    for index, entry in enumerate(priority_entries):
        path = _normalise_priority_path(entry)
        if not path:
            continue
        if path not in nodes_by_path:
            LOGGER.warning(
                "Priority entry '%s' does not match any configured taxonomy path", entry
            )
        priority_map[path] = index

    model = TaxonomyModel(
        nodes_by_path=nodes_by_path,
        priority_map=priority_map,
        alias_rules=alias_rules,
        max_depth=max_depth,
    )

    if available_taxonomy:
        categories, subcategories, item_categories = available_taxonomy
        for path, node in nodes_by_path.items():
            if len(path) == 1 and path[0] not in categories:
                LOGGER.warning(
                    "Configured category '%s' was not returned by Freshservice metadata", path[0]
                )
            elif len(path) == 2:
                parent = path[0]
                expected = subcategories.get(parent, [])
                if node.label not in expected:
                    LOGGER.warning(
                        "Configured subcategory '%s' (parent '%s') not present in Freshservice metadata",
                        node.label,
                        parent,
                    )
            elif len(path) == 3:
                parent_key = (path[0], path[1])
                expected = item_categories.get(parent_key, [])
                if node.label not in expected:
                    LOGGER.warning(
                        "Configured item category '%s' (parent '%s > %s') not present in Freshservice metadata",
                        node.label,
                        path[0],
                        path[1],
                    )

    return model


def _build_model_from_metadata(
    available_taxonomy: Tuple[
        List[str],
        Dict[str | None, List[str]],
        Dict[Tuple[str | None, str | None], List[str]],
    ],
    *,
    max_depth: int,
) -> TaxonomyModel:
    """Create a basic taxonomy model from Freshservice field metadata."""

    categories, subcategories, item_categories = available_taxonomy
    nodes_by_path: Dict[Tuple[str, ...], TaxonomyNode] = {}

    def _ensure_node(label: Optional[str], parent_path: Tuple[str, ...]) -> Optional[TaxonomyNode]:
        if label is None:
            return None
        text = str(label).strip()
        if not text:
            return None
        path = parent_path + (text,)
        if len(path) > max_depth:
            LOGGER.warning(
                "Ignoring taxonomy label '%s' because it exceeds max depth %s", text, max_depth
            )
            return None
        node = nodes_by_path.get(path)
        if node is None:
            keywords = _derive_label_keywords(text)
            node = TaxonomyNode(
                label=text,
                path=path,
                keywords=keywords,
                keyword_norms=[keyword.lower() for keyword in keywords],
                regexes=[],
                aliases=[],
                children=[],
            )
            nodes_by_path[path] = node
            if parent_path:
                parent = nodes_by_path.get(parent_path)
                if parent is None:
                    # Parent may not exist if the metadata omitted it; create implicitly.
                    parent_label = parent_path[-1]
                    parent = _ensure_node(parent_label, parent_path[:-1])
                if parent:
                    parent.children.append(node)
        return node

    for category in categories:
        _ensure_node(category, ())

    for parent_label, entries in subcategories.items():
        parent_path: Tuple[str, ...] = ()
        parent_node = _ensure_node(parent_label, ()) if parent_label else None
        if parent_node:
            parent_path = parent_node.path
        for entry in entries:
            _ensure_node(entry, parent_path)

    for (category_parent, sub_parent), entries in item_categories.items():
        current_path: Tuple[str, ...] = ()
        category_node = _ensure_node(category_parent, ()) if category_parent else None
        if category_node:
            current_path = category_node.path
        sub_node: Optional[TaxonomyNode] = None
        if sub_parent:
            sub_node = _ensure_node(sub_parent, current_path)
            if sub_node:
                current_path = sub_node.path
        for entry in entries:
            _ensure_node(entry, current_path)

    return TaxonomyModel(
        nodes_by_path=nodes_by_path,
        priority_map={},
        alias_rules=[],
        max_depth=max_depth,
    )


def _derive_label_keywords(label: str, extras: Optional[Iterable[str]] = None) -> List[str]:
    tokens = _split_label_tokens(label)
    keywords: List[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        norm = term.strip().lower()
        if not norm:
            return
        if norm not in seen:
            keywords.append(term.strip())
            seen.add(norm)

    def _add_with_aliases(term: str) -> None:
        _add(term)
        norm = term.strip().lower()
        for alias in _SIMPLE_ALIASES.get(norm, []):
            _add(alias)

    base = label.strip()
    if base:
        _add_with_aliases(base)
        for variant in _delimiter_variants(base):
            _add_with_aliases(variant)

    lower_label = base.lower()
    if lower_label and lower_label != base:
        _add_with_aliases(lower_label)

    for token in tokens:
        _add_with_aliases(token)
        for plural in _plural_forms(token):
            _add_with_aliases(plural)
        for expansion in _VENDOR_EXPANSIONS.get(token, []):
            _add_with_aliases(expansion)

    max_span = min(4, len(tokens))
    for span in range(2, max_span + 1):
        for index in range(len(tokens) - span + 1):
            phrase_tokens = tokens[index : index + span]
            phrase = " ".join(phrase_tokens)
            _add_with_aliases(phrase)
            _add_with_aliases(phrase.replace(" ", ""))
            _add_with_aliases(phrase.replace(" ", "-"))

    if extras:
        for value in extras:
            _add_with_aliases(str(value))

    return keywords


def _split_label_tokens(label: str) -> List[str]:
    raw_tokens = TOKEN_PATTERN.findall(label)
    tokens: List[str] = []

    for token in raw_tokens:
        for part in _split_camel_case(token):
            lowered = part.lower()
            if lowered:
                tokens.append(lowered)
    return tokens


def _split_camel_case(token: str) -> List[str]:
    pattern = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|\d+")
    parts = pattern.findall(token)
    return parts or [token]


def _delimiter_variants(text: str) -> List[str]:
    variants: List[str] = []
    lowered = text.lower()
    for source, replacements in (("-", [" ", ""]), (" ", ["-", ""]), ("/", [" ", "-"])):
        if source in text:
            for replacement in replacements:
                variant = text.replace(source, replacement)
                if variant.lower() != lowered:
                    variants.append(variant)
    return variants


def _plural_forms(token: str) -> List[str]:
    forms: List[str] = []
    if token.endswith("s") and len(token) > 1:
        forms.append(token[:-1])
    elif len(token) > 2:
        if token.endswith("y"):
            forms.append(token[:-1] + "ies")
        forms.append(token + "s")
    return forms

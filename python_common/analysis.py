"""Keyword and taxonomy driven analysis for Freshservice tickets."""
from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from dateutil import parser as date_parser
from rapidfuzz import fuzz

from .taxonomy import TaxonomyModel

LOGGER = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
CLASSIFICATION_SNIPPET = 240

_FUZZY_WHITELIST = {
    "anyware",
    "autodesk",
    "cato",
    "cinema4d",
    "citrix",
    "intune",
    "mimecast",
    "teradici",
    "unreal",
    "vray",
}

_FUZZY_SCORE_THRESHOLD = 80
_FUZZY_MAX_BOOST = 0.12
_FUZZY_NEW_MATCH_BASE = 0.45
_FUZZY_NEW_MATCH_CAP = 0.6


def _to_utc_display(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = date_parser.parse(str(value))
        except (ValueError, TypeError):  # pragma: no cover - defensive
            LOGGER.debug("Unable to parse datetime value %r", value)
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass
class TicketRecord:
    id: int
    subject: str
    description_text: str
    category: Optional[str]
    sub_category: Optional[str]
    item_category: Optional[str]
    created_at_utc: Optional[str] = None
    final_category: str = ""
    final_sub_category: str = ""
    final_item_category: str = ""

    @classmethod
    def from_api(cls, payload: Dict[str, Any]) -> "TicketRecord":
        description = payload.get("description_text") or payload.get("description") or ""
        created_at = _to_utc_display(payload.get("created_at"))
        return cls(
            id=payload.get("id"),
            subject=payload.get("subject", ""),
            description_text=description,
            category=payload.get("category"),
            sub_category=payload.get("sub_category"),
            item_category=payload.get("item_category"),
            created_at_utc=created_at,
        )


@dataclass
class SuggestedCategory:
    category: Optional[str]
    sub_category: Optional[str]
    item_category: Optional[str]
    confidence: float
    rationale: str


@dataclass
class _MatchResult:
    path: Tuple[str, ...]
    confidence: float
    rationale: str
    matched_keywords: List[str]
    matched_regexes: List[str]
    matched_alias: Optional[str] = None
    alias_legacy: bool = False
    alias_note: Optional[str] = None
    tfidf_score: float = 0.0
    priority_override: Optional[int] = None


@dataclass(frozen=True)
class _ProximityRule:
    path: Tuple[str, ...]
    term_groups: Tuple[Tuple[Tuple[str, ...], ...], ...]
    window: int = 6
    boost: float = 0.1
    description: str = "proximity"


@dataclass
class _TfidfArtifacts:
    ticket_vectors: List[Dict[str, float]]
    prototype_vectors: List[Dict[str, float]]
    prototype_paths: List[Tuple[str, ...]]


class TicketAnalyzer:
    """Perform taxonomy guided ticket analysis."""

    def __init__(
        self,
        *,
        taxonomy: TaxonomyModel,
        keyword_min_length: int = 4,
        min_keyword_frequency: int = 3,
        max_suggestions_per_ticket: int = 3,
        stop_words: Optional[Iterable[str]] = None,
        keyword_overrides: Optional[Dict[str, Dict[str, str]]] = None,
        tfidf_leaf_threshold: float = 0.05,
        tfidf_weight: float = 0.35,
    ) -> None:
        self.taxonomy = taxonomy
        self.keyword_min_length = keyword_min_length
        self.min_keyword_frequency = min_keyword_frequency
        self.max_suggestions_per_ticket = max(1, max_suggestions_per_ticket)
        self.stop_words = {word.lower() for word in (stop_words or [])}
        self.keyword_overrides = keyword_overrides or {}
        self.tfidf_leaf_threshold = max(0.0, float(tfidf_leaf_threshold))
        self.tfidf_weight = max(0.0, float(tfidf_weight))
        self._proximity_rules = self._build_proximity_rules()
        self._fuzzy_terms = self._build_fuzzy_terms()

    @staticmethod
    def _raw_tokens(text: str) -> List[str]:
        return [token.lower() for token in TOKEN_PATTERN.findall(text or "")]

    def _filter_tokens(self, tokens: Sequence[str]) -> List[str]:
        filtered = [t for t in tokens if len(t) >= self.keyword_min_length and t not in self.stop_words]
        LOGGER.debug("Tokenized text into %s filtered tokens", len(filtered))
        return filtered

    def tokenize(self, text: str) -> List[str]:
        return self._filter_tokens(self._raw_tokens(text))

    def keyword_counts(self, tickets: Iterable[TicketRecord]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for ticket in tickets:
            text = f"{ticket.subject or ''} {ticket.description_text or ''}"
            tokens = self.tokenize(text)
            counts.update(tokens)
        return counts

    def _matches_term(self, term: str, norm: str, *, text_lower: str, token_set: set[str]) -> bool:
        if not norm:
            return False
        if re.fullmatch(r"[A-Za-z0-9_]+", norm):
            return norm in token_set
        return norm in text_lower

    def _confidence(self, *, keyword_hits: int, depth: int) -> float:
        if depth >= 3:
            base = 0.9
        elif depth == 2:
            base = 0.8
        else:
            base = 0.65
        bonus = min(0.05 * max(0, keyword_hits - 1), 0.09)
        return round(min(base + bonus, 0.95), 2)

    def _build_rationale(
        self,
        *,
        path: Tuple[str, ...],
        matched_keywords: List[str],
        matched_regexes: List[str],
        alias: Optional[str],
        alias_legacy: bool,
        alias_note: Optional[str],
    ) -> str:
        segments: List[str] = []
        if matched_keywords:
            segments.append(f"keywords {matched_keywords}")
        if matched_regexes:
            segments.append(f"regex {matched_regexes}")
        if alias:
            legacy_text = " (legacy)" if alias_legacy else ""
            segments.append(f"alias '{alias}'{legacy_text}")
        if alias_note:
            segments.append(alias_note)
        if not segments:
            segments.append("taxonomy rules")
        return f"Matched {' > '.join(path)} via " + ", ".join(segments)

    def _evaluate_aliases(
        self,
        *,
        text: str,
        text_lower: str,
        token_set: set[str],
    ) -> List[_MatchResult]:
        matches: List[_MatchResult] = []
        for rule in self.taxonomy.alias_rules:
            matched = False
            if rule.regex:
                if rule.regex.search(text):
                    matched = True
            else:
                norm = rule.alias.lower()
                if self._matches_term(rule.alias, norm, text_lower=text_lower, token_set=token_set):
                    matched = True
            if not matched:
                continue
            confidence = self._confidence(1, 0, len(rule.target_path), alias=True)
            rationale = self._build_rationale(
                path=rule.target_path,
                matched_keywords=[],
                matched_regexes=[],
                alias=rule.alias,
                alias_legacy=rule.legacy,
                alias_note=rule.note,
            )
            matches.append(
                _MatchResult(
                    path=rule.target_path,
                    confidence=confidence,
                    rationale=rationale,
                    matched_keywords=[],
                    matched_regexes=[],
                    matched_alias=rule.alias,
                    alias_legacy=rule.legacy,
                    alias_note=rule.note,
                )
            )
        return matches

    def _match_taxonomy(self, *, text: str, token_set: set[str]) -> Dict[Tuple[str, ...], _MatchResult]:
        matches: Dict[Tuple[str, ...], _MatchResult] = {}
        text_lower = text.lower()
        for node in self.taxonomy.iter_nodes():
            matched_keywords: List[str] = []
            for keyword, norm in zip(node.keywords, node.keyword_norms):
                if self._matches_term(keyword, norm, text_lower=text_lower, token_set=token_set):
                    matched_keywords.append(keyword)
            if not matched_keywords:
                continue
            confidence = self._confidence(keyword_hits=len(matched_keywords), depth=len(node.path))
            rationale = self._build_rationale(
                path=node.path,
                matched_keywords=matched_keywords,
                matched_regexes=[],
                alias=None,
                alias_legacy=False,
                alias_note=None,
            )
            matches[node.path] = _MatchResult(
                path=node.path,
                confidence=confidence,
                rationale=rationale,
                matched_keywords=matched_keywords,
                matched_regexes=[],
                tfidf_score=0.0,
            )
        return matches

    def _build_proximity_rules(self) -> List[_ProximityRule]:
        rules: List[_ProximityRule] = []

        def phrases(options: Sequence[str]) -> Tuple[Tuple[str, ...], ...]:
            results: List[Tuple[str, ...]] = []
            for option in options:
                tokens = tuple(self._raw_tokens(option))
                if tokens:
                    results.append(tokens)
            return tuple(results)

        rules.append(
            _ProximityRule(
                path=("Hardware", "Peripherals", "Audio / Video Devices"),
                term_groups=(
                    phrases(["teams"]),
                    phrases(["camera"]),
                ),
                description="teams/camera proximity",
            )
        )
        rules.append(
            _ProximityRule(
                path=("Software", "Productivity", "MS Office / Outlook"),
                term_groups=(
                    phrases(["outlook"]),
                    phrases(["signature"]),
                ),
                description="outlook signature proximity",
            )
        )
        rules.append(
            _ProximityRule(
                path=("Remote Access", "VPN (CATO)"),
                term_groups=(
                    phrases(["vpn", "cato"]),
                    phrases(["connect", "cannot", "failed"]),
                ),
                description="vpn connectivity proximity",
            )
        )
        rules.append(
            _ProximityRule(
                path=("Computer Management", "Drive Mapping"),
                term_groups=(
                    phrases(["drive"]),
                    phrases(["mapping", "smb"]),
                ),
                description="drive mapping proximity",
            )
        )
        rules.append(
            _ProximityRule(
                path=("Computer Management", "Intune Policy & Configuration"),
                term_groups=(
                    phrases(["intune"]),
                    phrases(["policy", "profile", "company portal"]),
                ),
                description="intune policy proximity",
            )
        )
        return rules

    def _build_fuzzy_terms(self) -> Dict[Tuple[str, ...], set[str]]:
        mapping: Dict[Tuple[str, ...], set[str]] = {}
        whitelist = _FUZZY_WHITELIST
        if not whitelist:
            return mapping
        for node in self.taxonomy.iter_nodes():
            candidates: set[str] = set()
            for keyword in node.keywords:
                for token in self._raw_tokens(keyword.lower()):
                    if token in whitelist:
                        candidates.add(token)
            for token in self._raw_tokens(node.label.lower()):
                if token in whitelist:
                    candidates.add(token)
            if candidates:
                mapping[node.path] = candidates
        return mapping

    @staticmethod
    def _phrase_positions(tokens: Sequence[str], phrase: Tuple[str, ...]) -> List[int]:
        if not phrase:
            return []
        window = len(phrase)
        if window == 0 or window > len(tokens):
            return []
        positions: List[int] = []
        token_tuple = tuple(tokens)
        for index in range(len(tokens) - window + 1):
            if token_tuple[index : index + window] == phrase:
                positions.append(index)
        return positions

    def _apply_proximity_boosts(
        self,
        match_map: Dict[Tuple[str, ...], _MatchResult],
        tokens: Sequence[str],
    ) -> None:
        if not tokens:
            return
        for rule in self._proximity_rules:
            if not rule.term_groups or len(rule.term_groups) < 2:
                continue
            if not self.taxonomy.get_node(rule.path):
                continue
            positions_groups: List[List[int]] = []
            for group in rule.term_groups:
                group_positions: List[int] = []
                for phrase in group:
                    group_positions.extend(self._phrase_positions(tokens, phrase))
                if not group_positions:
                    positions_groups = []
                    break
                positions_groups.append(sorted(set(group_positions)))
            if not positions_groups:
                continue
            anchor_positions = positions_groups[0]
            found = False
            for anchor in anchor_positions:
                within_window = True
                for others in positions_groups[1:]:
                    if not any(abs(anchor - pos) <= rule.window for pos in others):
                        within_window = False
                        break
                if within_window:
                    found = True
                    break
            if not found:
                continue
            match = match_map.get(rule.path)
            if match:
                original = match.confidence
                match.confidence = round(min(match.confidence + rule.boost, 0.95), 2)
                match.rationale += f"; {rule.description}"
                LOGGER.debug(
                    "Applied proximity boost %.2f to %s (%.2f -> %.2f)",
                    rule.boost,
                    " > ".join(rule.path),
                    original,
                    match.confidence,
                )
            else:
                depth = len(rule.path)
                base = max(self._confidence(keyword_hits=1, depth=depth) - 0.1, 0.5)
                match_map[rule.path] = _MatchResult(
                    path=rule.path,
                    confidence=round(base, 2),
                    rationale=f"Matched {' > '.join(rule.path)} via {rule.description}",
                    matched_keywords=[],
                    matched_regexes=[],
                    tfidf_score=0.0,
                )
                LOGGER.debug(
                    "Created proximity-based match for %s with confidence %.2f",
                    " > ".join(rule.path),
                    match_map[rule.path].confidence,
                )

    def _apply_fuzzy_matches(
        self,
        match_map: Dict[Tuple[str, ...], _MatchResult],
        raw_tokens: Sequence[str],
        token_set: set[str],
    ) -> None:
        if not self._fuzzy_terms or not raw_tokens:
            return
        unique_tokens = set(raw_tokens)
        fuzzy_tokens = {token for token in unique_tokens if len(token) >= 3}
        for path, seeds in self._fuzzy_terms.items():
            if not seeds:
                continue
            node = self.taxonomy.get_node(path)
            if not node:
                continue
            best_score = 0
            best_seed: Optional[str] = None
            best_token: Optional[str] = None
            for seed in seeds:
                if seed in token_set:
                    best_score = 100
                    best_seed = seed
                    best_token = seed
                    break
                for token in fuzzy_tokens:
                    if token == seed:
                        best_score = 100
                        best_seed = seed
                        best_token = token
                        break
                    score = fuzz.partial_ratio(seed, token)
                    if score > best_score:
                        best_score = score
                        best_seed = seed
                        best_token = token
                if best_score == 100:
                    break
            if best_score < _FUZZY_SCORE_THRESHOLD or not best_seed or not best_token:
                continue
            boost = min(_FUZZY_MAX_BOOST, (best_score / 100.0) * _FUZZY_MAX_BOOST)
            rationale_note = f"fuzzy {best_seed}->{best_token} {best_score:.0f}"
            if path in match_map:
                match = match_map[path]
                original = match.confidence
                match.confidence = round(min(match.confidence + boost, 0.95), 2)
                match.rationale += f"; {rationale_note}"
                LOGGER.debug(
                    "Applied fuzzy boost %.2f to %s (%.2f -> %.2f) via %s",
                    boost,
                    " > ".join(path),
                    original,
                    match.confidence,
                    rationale_note,
                )
                continue
            if node.children:
                continue
            confidence = min(_FUZZY_NEW_MATCH_CAP, _FUZZY_NEW_MATCH_BASE + boost)
            match_map[path] = _MatchResult(
                path=path,
                confidence=round(confidence, 2),
                rationale=f"Matched {' > '.join(path)} via {rationale_note}",
                matched_keywords=[],
                matched_regexes=[],
                tfidf_score=0.0,
            )
            LOGGER.debug(
                "Created fuzzy match for %s with confidence %.2f",
                " > ".join(path),
                match_map[path].confidence,
            )

    def _apply_negative_keywords(
        self,
        match_map: Dict[Tuple[str, ...], _MatchResult],
        tokens: Sequence[str],
        text_lower: str,
    ) -> None:
        if not match_map:
            return
        token_set = set(tokens)
        citrix_path = ("Remote Access", "Citrix (Legacy)")
        if citrix_path in match_map and any(term in token_set for term in ("anyware", "rdp", "cato")):
            match = match_map[citrix_path]
            original = match.confidence
            match.confidence = round(max(match.confidence - 0.3, 0.05), 2)
            base_priority = self.taxonomy.priority_map.get(
                match.path, len(self.taxonomy.priority_map) + 1
            )
            match.priority_override = base_priority + len(self.taxonomy.priority_map) + 10
            match.rationale += "; demoted due to Anyware/RDP context"
            LOGGER.debug(
                "Applied Citrix demotion %.2f -> %.2f", original, match.confidence
            )

        if "not vpn" in text_lower:
            for match in match_map.values():
                if any("vpn" in part.lower() for part in match.path):
                    original = match.confidence
                    match.confidence = round(max(match.confidence - 0.45, 0.05), 2)
                    base_priority = self.taxonomy.priority_map.get(
                        match.path, len(self.taxonomy.priority_map) + 1
                    )
                    match.priority_override = base_priority + len(self.taxonomy.priority_map) + 5
                    match.rationale += "; reduced by 'not vpn' guard"
                    LOGGER.debug(
                        "Reduced VPN-related confidence %.2f -> %.2f due to 'not vpn'",
                        original,
                        match.confidence,
                    )

        if "not a teams issue" in text_lower or "not a teams problem" in text_lower:
            for match in match_map.values():
                if any("teams" in part.lower() for part in match.path):
                    original = match.confidence
                    match.confidence = round(max(match.confidence - 0.35, 0.05), 2)
                    base_priority = self.taxonomy.priority_map.get(
                        match.path, len(self.taxonomy.priority_map) + 1
                    )
                    match.priority_override = base_priority + len(self.taxonomy.priority_map) + 5
                    match.rationale += "; reduced by 'not a teams issue' guard"
                    LOGGER.debug(
                        "Reduced Teams-related confidence %.2f -> %.2f due to Teams guard",
                        original,
                        match.confidence,
                    )

    def _build_tfidf_artifacts(self, tickets: List[TicketRecord]) -> Optional[_TfidfArtifacts]:
        if not tickets:
            return None

        corpus: List[str] = []
        for ticket in tickets:
            text = f"{ticket.subject or ''} {ticket.description_text or ''}".strip().lower()
            corpus.append(text)

        if not any(corpus):
            return None

        token_docs: List[List[str]] = [self._raw_tokens(text) for text in corpus]
        if not any(token_docs):
            return None

        tokens_by_path: Dict[Tuple[str, ...], List[str]] = defaultdict(list)
        for ticket, tokens in zip(tickets, token_docs):
            path_parts: List[str] = []
            if ticket.category:
                path_parts.append(ticket.category)
                path_tuple = tuple(path_parts)
                if self.taxonomy.get_node(path_tuple):
                    tokens_by_path[path_tuple].extend(tokens)
            if ticket.sub_category:
                path_parts.append(ticket.sub_category)
                path_tuple = tuple(path_parts)
                if self.taxonomy.get_node(path_tuple):
                    tokens_by_path[path_tuple].extend(tokens)
            if ticket.item_category:
                path_parts.append(ticket.item_category)
                path_tuple = tuple(path_parts)
                if self.taxonomy.get_node(path_tuple):
                    tokens_by_path[path_tuple].extend(tokens)

        doc_freq: Counter[str] = Counter()
        for tokens in token_docs:
            for token in set(tokens):
                doc_freq[token] += 1

        doc_count = len(token_docs)
        if doc_count == 0 or not doc_freq:
            return None

        idf_map: Dict[str, float] = {}
        for token, freq in doc_freq.items():
            idf_map[token] = math.log((1 + doc_count) / (1 + freq)) + 1.0
        default_idf = math.log(1 + doc_count) + 1.0

        ticket_vectors = [
            self._build_tfidf_vector(tokens, idf_map=idf_map, default_idf=default_idf)
            for tokens in token_docs
        ]

        prototype_paths: List[Tuple[str, ...]] = []
        prototype_vectors: List[Dict[str, float]] = []
        for path, node in self.taxonomy.nodes_by_path.items():
            if node.children:
                continue
            seed_tokens: List[str] = []
            for keyword in node.keywords:
                seed_tokens.extend(self._raw_tokens(keyword.lower()))
            seed_tokens.extend(tokens_by_path.get(path, []))
            vector = self._build_tfidf_vector(
                seed_tokens,
                idf_map=idf_map,
                default_idf=default_idf,
            )
            if not vector:
                continue
            prototype_paths.append(path)
            prototype_vectors.append(vector)

        if not prototype_vectors:
            return None

        return _TfidfArtifacts(
            ticket_vectors=ticket_vectors,
            prototype_vectors=prototype_vectors,
            prototype_paths=prototype_paths,
        )

    def _compute_tfidf_scores(
        self, tickets: List[TicketRecord]
    ) -> Optional[List[Dict[Tuple[str, ...], float]]]:
        artifacts = self._build_tfidf_artifacts(tickets)
        if artifacts is None:
            return None
        score_maps: List[Dict[Tuple[str, ...], float]] = []
        for ticket_vector in artifacts.ticket_vectors:
            score_map: Dict[Tuple[str, ...], float] = {}
            for index, path in enumerate(artifacts.prototype_paths):
                proto_vector = artifacts.prototype_vectors[index]
                value = self._cosine_similarity(ticket_vector, proto_vector)
                if value > 0.0:
                    score_map[path] = value
            score_maps.append(score_map)
        return score_maps

    def _apply_tfidf_scores(
        self,
        match_map: Dict[Tuple[str, ...], _MatchResult],
        tfidf_scores: Dict[Tuple[str, ...], float],
    ) -> None:
        if not tfidf_scores:
            return

        for path, score in tfidf_scores.items():
            if score <= 0.0:
                continue
            node = self.taxonomy.get_node(path)
            if not node:
                continue
            if path in match_map:
                match = match_map[path]
                match.tfidf_score = max(match.tfidf_score, score)
                match.confidence = round(min(match.confidence + score * self.tfidf_weight, 0.95), 2)
                if "tf-idf" in match.rationale:
                    match.rationale += f" ({score:.2f})"
                else:
                    match.rationale += f"; tf-idf {score:.2f}"
            else:
                if node.children:
                    continue
                if score < self.tfidf_leaf_threshold:
                    continue
                confidence = round(min(0.5 + score * self.tfidf_weight, 0.9), 2)
                rationale = f"Matched {' > '.join(path)} via tf-idf similarity {score:.2f}"
                match_map[path] = _MatchResult(
                    path=path,
                    confidence=confidence,
                    rationale=rationale,
                    matched_keywords=[],
                    matched_regexes=[],
                    matched_alias=None,
                    alias_legacy=False,
                    alias_note=None,
                    tfidf_score=score,
                )

    @staticmethod
    def _cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
        if not vec_a or not vec_b:
            return 0.0
        if len(vec_a) > len(vec_b):
            vec_a, vec_b = vec_b, vec_a
        total = 0.0
        for token, weight in vec_a.items():
            other = vec_b.get(token)
            if other:
                total += weight * other
        return total

    @staticmethod
    def _build_tfidf_vector(
        tokens: Sequence[str],
        *,
        idf_map: Dict[str, float],
        default_idf: float,
    ) -> Dict[str, float]:
        if not tokens:
            return {}
        counts = Counter(tokens)
        total = sum(counts.values())
        if total == 0:
            return {}
        weights: Dict[str, float] = {}
        for token, count in counts.items():
            tf = count / total
            idf = idf_map.get(token, default_idf)
            weight = tf * idf
            if weight > 0:
                weights[token] = weight
        if not weights:
            return {}
        norm = math.sqrt(sum(value * value for value in weights.values()))
        if norm == 0:
            return {}
        return {token: value / norm for token, value in weights.items()}

    def _sorted_matches(self, matches: Dict[Tuple[str, ...], _MatchResult]) -> List[_MatchResult]:
        default_priority = len(self.taxonomy.priority_map) + 1

        def sort_key(result: _MatchResult) -> Tuple[int, int, float, Tuple[str, ...]]:
            if result.priority_override is not None:
                priority_rank = result.priority_override
            else:
                priority_rank = self.taxonomy.priority_map.get(result.path, default_priority)
            depth = len(result.path)
            return (priority_rank, -depth, -result.confidence, result.path)

        return sorted(matches.values(), key=sort_key)

    def _fallback_match(self) -> Optional[_MatchResult]:
        target_path: Optional[Tuple[str, ...]] = None
        for path in self.taxonomy.nodes_by_path:
            if len(path) >= 2 and path[0] == "General IT" and path[1] == "Questions":
                target_path = path[:2]
                break
        if target_path is None:
            for path in self.taxonomy.nodes_by_path:
                if len(path) == 1 and path[0] == "General IT":
                    target_path = path
                    break
        if target_path is None:
            return None
        depth = len(target_path)
        confidence = {3: 0.6, 2: 0.55, 1: 0.5}.get(depth, 0.45)
        rationale = "Fallback to General IT > Questions"
        return _MatchResult(
            path=target_path,
            confidence=confidence,
            rationale=rationale,
            matched_keywords=[],
            matched_regexes=[],
        )

    def suggest_categories(
        self,
        tickets: Iterable[TicketRecord],
        *,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[int, List[SuggestedCategory]]:
        ticket_list = list(tickets)
        tfidf_scores = self._compute_tfidf_scores(ticket_list)

        suggestions: Dict[int, List[SuggestedCategory]] = {}
        total_tickets = len(ticket_list)
        for index, ticket in enumerate(ticket_list, start=1):
            subject = ticket.subject or ""
            description = ticket.description_text or ""
            combined_text = f"{subject} {description}".strip()
            raw_tokens = self._raw_tokens(combined_text)
            token_set = set(raw_tokens)
            LOGGER.debug(
                "Ticket %s classification input subject=%r description_excerpt=%r",
                ticket.id,
                subject,
                (description or "")[:CLASSIFICATION_SNIPPET],
            )
            match_map = self._match_taxonomy(text=combined_text, token_set=token_set)
            self._apply_proximity_boosts(match_map, raw_tokens)
            self._apply_fuzzy_matches(match_map, raw_tokens, token_set)
            tfidf_map: Dict[Tuple[str, ...], float] = {}
            if tfidf_scores is not None:
                tfidf_map = tfidf_scores[index - 1]
            self._apply_tfidf_scores(match_map, tfidf_map)
            self._apply_negative_keywords(match_map, raw_tokens, combined_text.lower())
            ordered_matches = self._sorted_matches(match_map)
            primary: Optional[_MatchResult] = None
            for depth in (3, 2, 1):
                for match in ordered_matches:
                    if len(match.path) == depth:
                        primary = match
                        break
                if primary:
                    break
            if primary is None:
                fallback = self._fallback_match()
                if fallback:
                    ordered_matches.insert(0, fallback)
                    primary = fallback
            suggestion_list: List[SuggestedCategory] = []
            for match in ordered_matches[: self.max_suggestions_per_ticket]:
                category = match.path[0] if len(match.path) >= 1 else None
                sub_category = match.path[1] if len(match.path) >= 2 else None
                item_category = match.path[2] if len(match.path) >= 3 else None
                suggestion_list.append(
                    SuggestedCategory(
                        category=category,
                        sub_category=sub_category,
                        item_category=item_category,
                        confidence=round(match.confidence, 2),
                        rationale=match.rationale,
                    )
                )
                LOGGER.debug(
                    "Ticket %s matched path=%s confidence=%.2f details=%s",
                    ticket.id,
                    " > ".join(match.path),
                    match.confidence,
                    match.rationale,
                )
            if primary:
                top = suggestion_list[0]
                ticket.final_category = top.category or ""
                ticket.final_sub_category = top.sub_category or ""
                ticket.final_item_category = top.item_category or ""
                path_summary = " > ".join(
                    part for part in (top.category, top.sub_category, top.item_category) if part
                ) or "<no category>"
                LOGGER.info(
                    "Ticket %s classified as %s (confidence %.2f)",
                    ticket.id,
                    path_summary,
                    top.confidence,
                )
            suggestions[ticket.id] = suggestion_list
            if progress_callback:
                progress_callback(index, total_tickets)
        return suggestions

    def detect_repeating_keywords(self, tickets: Iterable[TicketRecord]) -> List[Tuple[str, int]]:
        counts = Counter()
        for ticket in tickets:
            tokens = self.tokenize(f"{ticket.subject or ''} {ticket.description_text or ''}")
            counts.update(set(tokens))
        repeating = [item for item in counts.items() if item[1] >= self.min_keyword_frequency]
        repeating.sort(key=lambda item: item[1], reverse=True)
        LOGGER.info("Identified %s repeating keyword patterns", len(repeating))
        return repeating

    @staticmethod
    def extract_existing_categories(tickets: Iterable[TicketRecord]) -> Dict[str, set[str]]:
        existing: Dict[str, set[str]] = defaultdict(set)
        for ticket in tickets:
            if ticket.category:
                existing["category"].add(ticket.category)
            if ticket.sub_category:
                existing["sub_category"].add(ticket.sub_category)
            if ticket.item_category:
                existing["item_category"].add(ticket.item_category)
        return existing


from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, List, Optional, Tuple
import types
import sys
from datetime import datetime
from importlib import util

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_PATH = PROJECT_ROOT / "python_common" / "analysis.py"
TAXONOMY_PATH = PROJECT_ROOT / "python_common" / "taxonomy.py"
REPORTING_PATH = PROJECT_ROOT / "python_common" / "reporting.py"

package = types.ModuleType("python_common")
package.__path__ = [str(PROJECT_ROOT / "python_common")]
sys.modules.setdefault("python_common", package)

dateutil_module = types.ModuleType("dateutil")
parser_module = types.ModuleType("parser")

rapidfuzz_module = types.ModuleType("rapidfuzz")
fuzz_module = types.ModuleType("rapidfuzz.fuzz")


def _simple_ratio(lhs: object, rhs: object) -> int:
    left = str(lhs or "")
    right = str(rhs or "")
    if not left and not right:
        return 100
    if not left or not right:
        return 0
    from difflib import SequenceMatcher

    return int(SequenceMatcher(None, left, right).ratio() * 100)


fuzz_module.token_set_ratio = _simple_ratio  # type: ignore[attr-defined]
fuzz_module.partial_ratio = _simple_ratio  # type: ignore[attr-defined]
rapidfuzz_module.fuzz = fuzz_module  # type: ignore[attr-defined]
sys.modules.setdefault("rapidfuzz", rapidfuzz_module)
sys.modules.setdefault("rapidfuzz.fuzz", fuzz_module)


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


parser_module.parse = _parse_datetime
parser_module.isoparse = _parse_datetime
dateutil_module.parser = parser_module
sys.modules.setdefault("dateutil", dateutil_module)
sys.modules.setdefault("dateutil.parser", parser_module)


def _load_module(name: str, path: Path):
    spec = util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


analysis_module = _load_module("python_common.analysis", ANALYSIS_PATH)
taxonomy_module = _load_module("python_common.taxonomy", TAXONOMY_PATH)
reporting_module = _load_module("python_common.reporting", REPORTING_PATH)

TicketAnalyzer = analysis_module.TicketAnalyzer
TicketRecord = analysis_module.TicketRecord
SuggestedCategory = analysis_module.SuggestedCategory
build_taxonomy_model = taxonomy_module.build_taxonomy_model
TicketReportWriter = reporting_module.TicketReportWriter


def make_ticket(
    *,
    ticket_id: int,
    subject: Optional[str],
    description: Optional[str],
    category: Optional[str] = None,
    sub_category: Optional[str] = None,
    item_category: Optional[str] = None,
) -> TicketRecord:
    return TicketRecord(
        id=ticket_id,
        subject=subject or "",
        description_text=description or "",
        category=category,
        sub_category=sub_category,
        item_category=item_category,
    )


def build_metadata_taxonomy(
    *,
    categories: Iterable[str],
    subcategories: Iterable[Tuple[str, Iterable[str]]],
    item_categories: Iterable[Tuple[Tuple[str, str], Iterable[str]]],
):
    category_list = list(categories)
    subcategory_map = {parent: list(children) for parent, children in subcategories}
    item_map = {key: list(children) for key, children in item_categories}
    return category_list, subcategory_map, item_map


def assert_top_path(
    suggestions: List[SuggestedCategory],
    *,
    expected_category: str,
    expected_subcategory: Optional[str],
    expected_item: Optional[str],
) -> None:
    assert suggestions, "Expected at least one suggestion"
    first = suggestions[0]
    assert first.category == expected_category
    assert first.sub_category == expected_subcategory
    assert first.item_category == expected_item


def test_ticket_record_parses_created_at_to_utc() -> None:
    payload = {
        "id": 1,
        "subject": "Example",
        "description": "Body",
        "category": None,
        "created_at": "2024-05-01T17:30:00-04:00",
    }

    record = TicketRecord.from_api(payload)

    assert record.created_at_utc == "2024-05-01 21:30:00 UTC"


def test_leaf_level_match_preferred() -> None:
    taxonomy = build_taxonomy_model(
        None,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Remote Access", "Software"],
            subcategories=[
                ("Remote Access", ["VPN (CATO)"]),
                ("Software", ["Productivity"]),
            ],
            item_categories=[
                (("Software", "Productivity"), ["MS Office / Outlook"]),
            ],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy, max_suggestions_per_ticket=3)
    tickets = [
        make_ticket(
            ticket_id=200,
            subject="VPN cato tunnel keeps dropping",
            description="user reports vpn outage on cato service",
        ),
        make_ticket(
            ticket_id=201,
            subject="Need outlook signature updated",
            description="Please adjust company logo in Outlook signature",
        ),
    ]

    suggestions = analyzer.suggest_categories(tickets)

    assert_top_path(
        suggestions[200],
        expected_category="Remote Access",
        expected_subcategory="VPN (CATO)",
        expected_item=None,
    )
    assert tickets[0].final_category == "Remote Access"
    assert tickets[0].final_sub_category == "VPN (CATO)"
    assert tickets[0].final_item_category == ""

    assert_top_path(
        suggestions[201],
        expected_category="Software",
        expected_subcategory="Productivity",
        expected_item="MS Office / Outlook",
    )
    assert tickets[1].final_item_category == "MS Office / Outlook"


def test_hardware_match_with_special_characters() -> None:
    taxonomy = build_taxonomy_model(
        None,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Hardware"],
            subcategories=[("Hardware", ["Peripherals"])],
            item_categories=[(("Hardware", "Peripherals"), ["Audio / Video Devices"])],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy, max_suggestions_per_ticket=1)
    ticket = make_ticket(
        ticket_id=310,
        subject="Teams camera not working",
        description="Conference room teams camera audio / video device offline",
    )

    suggestions = analyzer.suggest_categories([ticket])

    assert_top_path(
        suggestions[310],
        expected_category="Hardware",
        expected_subcategory="Peripherals",
        expected_item="Audio / Video Devices",
    )
    assert ticket.final_category == "Hardware"
    assert ticket.final_sub_category == "Peripherals"
    assert ticket.final_item_category == "Audio / Video Devices"


def test_proximity_rules_create_expected_leaf_matches() -> None:
    config = {
        "priority_order": [
            "Remote Access > Citrix (Legacy)",
            "Remote Access > Anyware/RDP VM",
        ],
        "tree": [
            {
                "label": "Hardware",
                "children": [
                    {
                        "label": "Peripherals",
                        "children": [
                            {
                                "label": "Audio / Video Devices",
                                "keywords": ["audio video devices"],
                            }
                        ],
                    }
                ],
            },
            {
                "label": "Software",
                "children": [
                    {
                        "label": "Productivity",
                        "children": [
                            {
                                "label": "MS Office / Outlook",
                                "keywords": ["outlook signature"],
                            },
                            {
                                "label": "Other",
                                "keywords": ["software"],
                            },
                        ],
                    }
                ],
            },
            {
                "label": "Remote Access",
                "children": [
                    {"label": "VPN (CATO)", "keywords": ["vpn"]},
                    {"label": "Anyware/RDP VM", "keywords": ["anyware"]},
                    {"label": "Citrix (Legacy)", "keywords": ["citrix"]},
                ],
            },
            {
                "label": "Computer Management",
                "children": [
                    {"label": "Drive Mapping", "keywords": ["mapping"]},
                    {
                        "label": "Intune Policy & Configuration",
                        "keywords": ["intune policy"],
                    },
                ],
            },
        ],
    }
    taxonomy = build_taxonomy_model(
        config,
        available_taxonomy=build_metadata_taxonomy(
            categories=[
                "Hardware",
                "Software",
                "Remote Access",
                "Computer Management",
            ],
            subcategories=[
                ("Hardware", ["Peripherals"]),
                ("Software", ["Productivity"]),
                (
                    "Remote Access",
                    ["VPN (CATO)", "Anyware/RDP VM", "Citrix (Legacy)"],
                ),
                (
                    "Computer Management",
                    ["Drive Mapping", "Intune Policy & Configuration"],
                ),
            ],
            item_categories=[
                (("Hardware", "Peripherals"), ["Audio / Video Devices"]),
                (("Software", "Productivity"), ["MS Office / Outlook", "Other"]),
            ],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy, max_suggestions_per_ticket=3)

    tickets = [
        make_ticket(
            ticket_id=601,
            subject="Teams camera offline",
            description="Teams camera cannot connect in meeting",
        ),
        make_ticket(
            ticket_id=602,
            subject="Need new Outlook signature",
            description="Please update the outlook signature with new logo",
        ),
        make_ticket(
            ticket_id=603,
            subject="VPN cannot connect",
            description="vpn failed to connect to cato gateway",
        ),
        make_ticket(
            ticket_id=604,
            subject="Drive mapping smb access",
            description="user drive mapping for smb share not working",
        ),
        make_ticket(
            ticket_id=605,
            subject="Intune profile issue",
            description="Company portal intune profile policy not applying",
        ),
    ]

    suggestions = analyzer.suggest_categories(tickets)

    assert_top_path(
        suggestions[601],
        expected_category="Hardware",
        expected_subcategory="Peripherals",
        expected_item="Audio / Video Devices",
    )
    assert_top_path(
        suggestions[602],
        expected_category="Software",
        expected_subcategory="Productivity",
        expected_item="MS Office / Outlook",
    )
    assert_top_path(
        suggestions[603],
        expected_category="Remote Access",
        expected_subcategory="VPN (CATO)",
        expected_item=None,
    )
    assert_top_path(
        suggestions[604],
        expected_category="Computer Management",
        expected_subcategory="Drive Mapping",
        expected_item=None,
    )
    assert_top_path(
        suggestions[605],
        expected_category="Computer Management",
        expected_subcategory="Intune Policy & Configuration",
        expected_item=None,
    )


def test_negative_keywords_demote_citrix_when_anyware_present() -> None:
    config = {
        "priority_order": [
            "Remote Access > Citrix (Legacy)",
            "Remote Access > Anyware/RDP VM",
        ],
        "tree": [
            {
                "label": "Remote Access",
                "children": [
                    {"label": "Anyware/RDP VM", "keywords": ["anyware"]},
                    {"label": "Citrix (Legacy)", "keywords": ["citrix"]},
                ],
            }
        ],
    }
    taxonomy = build_taxonomy_model(
        config,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Remote Access"],
            subcategories=[
                (
                    "Remote Access",
                    ["Anyware/RDP VM", "Citrix (Legacy)"],
                )
            ],
            item_categories=[],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy, max_suggestions_per_ticket=2)
    ticket = make_ticket(
        ticket_id=620,
        subject="Citrix login failing",
        description="citrix login failing when connecting to anyware host",
    )

    suggestions = analyzer.suggest_categories([ticket])

    assert_top_path(
        suggestions[620],
        expected_category="Remote Access",
        expected_subcategory="Anyware/RDP VM",
        expected_item=None,
    )
    # Ensure Citrix remains present but with reduced confidence due to the guard.
    citrix_entry = next(
        (
            suggestion
            for suggestion in suggestions[620]
            if suggestion.sub_category == "Citrix (Legacy)"
        ),
        None,
    )
    assert citrix_entry is not None
    assert "Anyware/RDP" in citrix_entry.rationale


def test_not_vpn_phrase_reduces_vpn_confidence() -> None:
    config = {
        "tree": [
            {
                "label": "Remote Access",
                "children": [
                    {"label": "VPN (CATO)", "keywords": ["vpn"]},
                ],
            },
            {
                "label": "Software",
                "children": [
                    {
                        "label": "Productivity",
                        "children": [
                            {
                                "label": "MS Office / Outlook",
                                "keywords": ["outlook signature"],
                            }
                        ],
                    }
                ],
            },
        ],
    }
    taxonomy = build_taxonomy_model(
        config,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Remote Access", "Software"],
            subcategories=[
                ("Remote Access", ["VPN (CATO)"]),
                ("Software", ["Productivity"]),
            ],
            item_categories=[
                (("Software", "Productivity"), ["MS Office / Outlook"]),
            ],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy, max_suggestions_per_ticket=2)
    ticket = make_ticket(
        ticket_id=640,
        subject="Not a VPN issue - need Outlook signature",
        description="This is not vpn related, only require outlook signature help",
    )

    suggestions = analyzer.suggest_categories([ticket])

    assert_top_path(
        suggestions[640],
        expected_category="Software",
        expected_subcategory="Productivity",
        expected_item="MS Office / Outlook",
    )
    vpn_entry = next(
        (
            suggestion
            for suggestion in suggestions[640]
            if suggestion.sub_category == "VPN (CATO)"
        ),
        None,
    )
    assert vpn_entry is not None
    assert vpn_entry.confidence < 0.5
    assert "not vpn" in vpn_entry.rationale.lower()

def test_fallback_to_general_it_questions() -> None:
    taxonomy = build_taxonomy_model(
        None,
        available_taxonomy=build_metadata_taxonomy(
            categories=["General IT"],
            subcategories=[("General IT", ["Questions"])],
            item_categories=[],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy)
    ticket = make_ticket(ticket_id=400, subject="Misc request", description="Please advise")

    suggestions = analyzer.suggest_categories([ticket])

    assert_top_path(
        suggestions[400],
        expected_category="General IT",
        expected_subcategory="Questions",
        expected_item=None,
    )
    assert ticket.final_category == "General IT"
    assert ticket.final_sub_category == "Questions"


def test_tfidf_similarity_uses_assigned_ticket_text() -> None:
    taxonomy = build_taxonomy_model(
        None,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Software"],
            subcategories=[("Software", ["Productivity"])],
            item_categories=[(("Software", "Productivity"), ["MS Office / Outlook"])],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy, max_suggestions_per_ticket=1)
    seeded_ticket = make_ticket(
        ticket_id=500,
        subject="Outlook mail bounce",
        description="Repeated mail bounce for external recipients",
        category="Software",
        sub_category="Productivity",
        item_category="MS Office / Outlook",
    )
    new_ticket = make_ticket(
        ticket_id=501,
        subject="Mail bounce for clients",
        description="mail bounce happens on all messages",
    )

    suggestions = analyzer.suggest_categories([new_ticket, seeded_ticket])

    assert_top_path(
        suggestions[501],
        expected_category="Software",
        expected_subcategory="Productivity",
        expected_item="MS Office / Outlook",
    )
    assert new_ticket.final_category == "Software"
    assert new_ticket.final_sub_category == "Productivity"
    assert new_ticket.final_item_category == "MS Office / Outlook"


def test_analysis_report_includes_final_columns_and_labels() -> None:
    taxonomy = build_taxonomy_model(
        None,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Security"],
            subcategories=[("Security", ["Remote Access"])],
            item_categories=[
                (("Security", "Remote Access"), ["Chaos (Vray / Corona)"])
            ],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy)
    ticket = make_ticket(
        ticket_id=510,
        subject="Vray license",
        description="Need chaos vray / corona renewal",
    )
    suggestions = analyzer.suggest_categories([ticket])

    with TemporaryDirectory() as tmpdir:
        writer = TicketReportWriter(output_directory=Path(tmpdir), report_name="analysis.csv")
        analysis_path = writer.write_analysis([ticket], suggestions, [])
        with analysis_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            row = next(reader)

    assert row["suggested_item_category"] == "Chaos (Vray / Corona)"
    assert row["final_item_category"] == "Chaos (Vray / Corona)"
    assert "final_category" in row
    assert "final_sub_category" in row


def test_review_template_preserves_final_values() -> None:
    taxonomy = build_taxonomy_model(
        None,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Software"],
            subcategories=[("Software", ["Creative & Design"])],
            item_categories=[(("Software", "Creative & Design"), ["Canva"])],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy)
    ticket = make_ticket(
        ticket_id=610,
        subject="Canva access",
        description="Need Canva sign-in fixed",
    )
    suggestions = analyzer.suggest_categories([ticket])

    with TemporaryDirectory() as tmpdir:
        writer = TicketReportWriter(output_directory=Path(tmpdir), report_name="analysis.csv")
        analysis_path = writer.write_analysis([ticket], suggestions, [])
        review_path = writer.create_review_template(analysis_path)
        with review_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            row = next(reader)

    assert row["final_category"] == "Software"
    assert row["final_sub_category"] == "Creative & Design"
    assert row["final_item_category"] == "Canva"
    assert row["manager_decision"] == "pending"
    assert fieldnames.count("final_category") == 1
    assert fieldnames.count("final_sub_category") == 1
    assert fieldnames.count("final_item_category") == 1


def test_fuzzy_matching_handles_common_typos() -> None:
    taxonomy = build_taxonomy_model(
        None,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Remote Access", "Software", "Computer Management"],
            subcategories=[
                ("Remote Access", ["Citrix (Legacy)", "HP Anyware / Teradici"]),
                ("Software", ["Creative & Design"]),
                ("Computer Management", ["Drive Mapping", "Intune Policy & Configuration"]),
            ],
            item_categories=[
                (("Software", "Creative & Design"), ["Chaos (Vray / Corona)", "Autodesk"]),
            ],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy, max_suggestions_per_ticket=3)
    tickets = [
        make_ticket(
            ticket_id=701,
            subject="citirx connection failing",
            description="citirx desktop launch fails with credential loop",
        ),
        make_ticket(
            ticket_id=702,
            subject="vrary license renewal",
            description="Need help renewing vrary render license",
        ),
        make_ticket(
            ticket_id=703,
            subject="autdesk installer crash",
            description="Autdesk install fails for creative team",
        ),
    ]

    suggestions = analyzer.suggest_categories(tickets)

    assert_top_path(
        suggestions[701],
        expected_category="Remote Access",
        expected_subcategory="Citrix (Legacy)",
        expected_item=None,
    )
    assert_top_path(
        suggestions[702],
        expected_category="Software",
        expected_subcategory="Creative & Design",
        expected_item="Chaos (Vray / Corona)",
    )
    assert_top_path(
        suggestions[703],
        expected_category="Software",
        expected_subcategory="Creative & Design",
        expected_item="Autodesk",
    )


def test_fuzzy_matches_do_not_override_exact_hits() -> None:
    taxonomy = build_taxonomy_model(
        None,
        available_taxonomy=build_metadata_taxonomy(
            categories=["Computer Management"],
            subcategories=[
                ("Computer Management", ["Drive Mapping", "Intune Policy & Configuration"]),
            ],
            item_categories=[],
        ),
    )
    analyzer = TicketAnalyzer(taxonomy=taxonomy, max_suggestions_per_ticket=3)
    ticket = make_ticket(
        ticket_id=800,
        subject="Drive mapping down after intunne push",
        description="User drive mapping fails following intunne configuration deployment",
    )

    suggestions = analyzer.suggest_categories([ticket])

    assert_top_path(
        suggestions[800],
        expected_category="Computer Management",
        expected_subcategory="Drive Mapping",
        expected_item=None,
    )
    assert any(
        s.category == "Computer Management"
        and s.sub_category == "Intune Policy & Configuration"
        for s in suggestions[800]
    )

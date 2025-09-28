from __future__ import annotations

from importlib import util
from pathlib import Path
import sys
import types


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = PROJECT_ROOT / "python_common" / "taxonomy.py"

package = types.ModuleType("python_common")
package.__path__ = [str(PROJECT_ROOT / "python_common")]
sys.modules.setdefault("python_common", package)


def _load_module(name: str, path: Path):
    spec = util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


taxonomy_module = _load_module("python_common.taxonomy", TAXONOMY_PATH)

build_taxonomy_model = taxonomy_module.build_taxonomy_model


def test_build_taxonomy_model_from_metadata():
    available = (
        ["Software", "Hardware"],
        {"Software": ["Adobe", "VPN"], None: ["General"]},
        {("Software", "Adobe"): ["Photoshop", "Illustrator"]},
    )

    model = build_taxonomy_model(None, available_taxonomy=available)

    assert model.get_node(("Software",)) is not None
    assert model.get_node(("Hardware",)) is not None
    assert model.get_node(("Software", "Adobe")) is not None
    assert model.get_node(("Software", "Adobe", "Photoshop")) is not None
    # Subcategory without a parent should still create a node
    assert model.get_node(("General",)) is not None


def test_build_taxonomy_model_missing_config_without_metadata():
    try:
        build_taxonomy_model(None, available_taxonomy=None)
    except ValueError as exc:
        assert "missing" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("Expected ValueError when config and metadata are missing")


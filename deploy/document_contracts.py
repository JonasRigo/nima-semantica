"""Print schemas for the current public tools and installed components."""
import json
from pathlib import Path
from nima_semantica.component_contracts import manifest_for_component
from nima_semantica.tool_guide import public_tool_catalog


def catalog():
    root = Path(__file__).resolve().parents[1]
    components = [{"identity": path.stem, "wrapper": str(path.relative_to(root)),
                   "manifest": manifest_for_component(path.stem).model_dump(mode="json")}
                  for path in sorted((root / "deploy/langflow_components").rglob("*.py"))]
    return {"schema_version": 2, "components": components,
            "tools": [tool.model_dump(mode="json") for tool in public_tool_catalog()]}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Refresh generated checked-in contract references")
    args = parser.parse_args()
    value = catalog()
    if args.write:
        root = Path(__file__).resolve().parents[1] / "docs/reference"
        (root / "contracts.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        (root / "tools.json").write_text(json.dumps(value["tools"], indent=2) + "\n")
    else:
        print(json.dumps(value, indent=2, sort_keys=True))

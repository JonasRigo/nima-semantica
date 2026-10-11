"""Pin an independently provisioned Lean toolchain; never download or execute it."""

import argparse
import json
from pathlib import Path

from nima_semantica.lean_project import LeanProjectVerifier, PinnedDirectory, tree_digest


def configuration(toolchain: Path, policy=None) -> dict:
    toolchain = toolchain.resolve(strict=True)
    pin = tree_digest(toolchain)
    # Validate the layout and pin using the same rules as the service.
    from nima_semantica.execution_policy import LeanExecutionPolicy
    policy = LeanExecutionPolicy.model_validate(policy or LeanExecutionPolicy())
    LeanProjectVerifier(PinnedDirectory(toolchain, pin), policy=policy)
    return {
        "toolchain": {"path": str(toolchain), "sha256": pin},
        "libraries": [],
        "trusted_imports": ["Init"],
        "policy": policy.model_dump(mode="json"),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--policy", type=Path, help="Operator execution policy JSON")
    args = parser.parse_args(argv)
    value = configuration(args.toolchain, json.loads(args.policy.read_text()) if args.policy else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves existing administrator decisions.
    with args.output.open("x") as stream:
        stream.write(json.dumps(value, indent=2) + "\n")
    args.output.chmod(0o600)
    print("Created pinned Init-only configuration; live verification is still required.")


if __name__ == "__main__":
    main()

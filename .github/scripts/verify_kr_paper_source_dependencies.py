#!/usr/bin/env python3
"""Verify the exact KR PAPER source distributions before provider access."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path


class DependencyError(ValueError):
    pass


def verify(contract_path: Path, wheel_dir: Path, *, installed_versions=None) -> dict:
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    if (
        contract.get("contract_version") != "krx_information_system_source_candidate/1"
        or contract.get("status") != "DRAFT_NOT_RUNTIME_RATIFIED"
    ):
        raise DependencyError("CONTRACT_INVALID")
    packages = contract.get("dependency_lock", {}).get("packages")
    if not isinstance(packages, dict) or set(packages) != {"numpy", "pandas", "pykrx", "requests"}:
        raise DependencyError("DEPENDENCY_SET_INVALID")
    installed_versions = installed_versions or {
        name: importlib.metadata.version(name) for name in packages
    }
    result = []
    for name, expected in sorted(packages.items()):
        path = Path(wheel_dir) / expected["filename"]
        if not path.is_file():
            raise DependencyError(f"WHEEL_MISSING:{name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected["sha256"]:
            raise DependencyError(f"WHEEL_HASH_INVALID:{name}")
        if installed_versions.get(name) != expected["version"]:
            raise DependencyError(f"INSTALLED_VERSION_INVALID:{name}")
        result.append(
            {
                "package": name,
                "version": expected["version"],
                "filename": expected["filename"],
                "sha256": digest,
            }
        )
    return {
        "schema": "kr_paper_source_dependency_verification/1",
        "status": "PASS",
        "packages": result,
        "authority": {
            "runtime_regime_authorized": False,
            "trading_authorized": False,
            "order_authorized": False,
            "capital_authorized": False,
            "real_authorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("artifact/dependency-verification.json"))
    args = parser.parse_args()
    result = verify(args.contract, args.wheel_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.out.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise DependencyError("NO_OVERWRITE") from exc
    print("PASS_KR_PAPER_SOURCE_DEPENDENCIES:4/4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

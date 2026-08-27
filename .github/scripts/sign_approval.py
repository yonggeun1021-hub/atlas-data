#!/usr/bin/env python3
"""Mint a signed CIO approval -- run OFFLINE, on the CIO's own machine.

The Ed25519 private key must never enter CI.  That is the whole point: a job
that could sign is a job that could approve itself.  CI holds only the public
key, in the repo, at config/atlas_approval_pubkey.txt.

    # one time
    python3 sign_approval.py keygen --out ~/.atlas/approval_key
    #   -> writes the private key locally, prints the public key to commit

    # per approval
    python3 sign_approval.py sign --key ~/.atlas/approval_key \\
        --repo-root . --slot evening --decision-date 2026-08-27 --approved-by "CIO"

    # ruling on a post-delivery source change (drain reports the change key)
    python3 sign_approval.py resolve-change --key ~/.atlas/approval_key \\
        --repo-root . --slot evening --decision-date 2026-08-27 \\
        --change-key <key> --capital-impact NONE --approved-by "CIO" \\
        --action-taken "Portal note only; no stage or money change"
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_ed25519 as ed25519
import briefing_finalization as bf


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["keygen", "sign", "resolve-change"])
    parser.add_argument("--out")
    parser.add_argument("--key")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--slot", choices=list(bf.SUPPORTED_SLOTS))
    parser.add_argument("--decision-date")
    parser.add_argument("--approved-by", default="CIO")
    parser.add_argument("--decision", default="APPROVE", choices=["APPROVE", "DENY"])
    parser.add_argument("--change-key", help="resolve-change: post_delivery_change_key to rule on")
    parser.add_argument("--capital-impact", choices=list(bf.CAPITAL_IMPACT_VERDICTS),
                        help="resolve-change: does this change move an investment conclusion?")
    parser.add_argument("--action-taken", default="",
                        help="resolve-change: what was actually done (recorded, not inferred)")
    args = parser.parse_args()

    if args.command == "keygen":
        sk = secrets.token_bytes(32)
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(sk.hex(), encoding="utf-8")
        os.chmod(out, 0o600)
        print(f"private key written to {out} (mode 600) -- never commit this")
        pk = ed25519.publickey(sk)
        print(f"public key (commit to {bf.APPROVAL_PUBKEY_PATH}):\n{pk.hex()}")
        print(f"\nfingerprint (set as repo secret {bf.APPROVAL_FINGERPRINT_ENV}):\n"
              f"{bf.pubkey_fingerprint(pk)}")
        print("\nThe secret is the out-of-band anchor: a repo writer can edit the key file,\n"
              "but not the secret. Without it, key swaps are only detectable after the fact.")
        return 0

    repo_root = Path(args.repo_root).resolve()
    directory = bf.slot_dir(repo_root, args.decision_date, args.slot)

    if args.command == "resolve-change":
        if not (args.change_key and args.capital_impact):
            parser.error("resolve-change needs --change-key and --capital-impact")
        sk = bytes.fromhex(Path(args.key).expanduser().read_text(encoding="utf-8").strip())
        if args.capital_impact == "PRESENT" and not args.action_taken.strip():
            parser.error("--capital-impact PRESENT requires --action-taken: a ruling that "
                         "it moves an investment conclusion must say what was done")
        message = bf.change_resolution_message(
            bf.briefing_id(args.decision_date, args.slot), args.change_key,
            args.capital_impact, args.approved_by, args.action_taken, bf.CONTRACT_VERSION)
        body = {
            "contract_version": bf.CONTRACT_VERSION,
            "post_delivery_change_key": args.change_key,
            "capital_impact": args.capital_impact,
            "resolved_by": args.approved_by,
            "action_taken": args.action_taken,
            "redelivery": "FORBIDDEN",
            "signature": ed25519.sign(message, sk).hex(),
        }
        rev = bf._next_rev(directory, "post-delivery-resolution")
        path = directory / f"post-delivery-resolution-rev-{rev:03d}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")
        print(f"wrote {path}")
        print("action_taken is inside the signature -- editing it after signing invalidates "
              "the ruling.\nA ruling is not completion: the Portal projection receipt (and for "
              "PRESENT, a user alert receipt) are still required.")
        return 0
    draft = json.loads(bf._latest(directory, "draft").read_text(encoding="utf-8"))
    validation = json.loads(bf._latest(directory, "validation").read_text(encoding="utf-8"))
    sk = bytes.fromhex(Path(args.key).expanduser().read_text(encoding="utf-8").strip())

    message = bf.approval_message(draft["briefing_id"], draft["delivery_payload_sha256"],
                                  validation["rev"], args.approved_by, args.decision,
                                  bf.CONTRACT_VERSION)
    approval = {
        "contract_version": bf.CONTRACT_VERSION, "decision": args.decision,
        "approved_by": args.approved_by,
        "approves_payload_sha256": draft["delivery_payload_sha256"],
        "approves_validation_rev": validation["rev"],
        "signature": ed25519.sign(message, sk).hex(),
    }
    print(f"public key fingerprint: {bf.pubkey_fingerprint(ed25519.publickey(sk))}")
    rev = bf._next_rev(directory, "approval")
    path = directory / f"approval-rev-{rev:03d}.json"
    path.write_text(json.dumps(approval, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {path}\nreview the payload, then commit and push it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

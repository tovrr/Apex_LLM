"""manage_keys.py — CLI for Apex multi-tenant API key management.

Usage
-----
  python manage_keys.py add   --label quill-prod --plan pro
  python manage_keys.py list
  python manage_keys.py revoke --hash <full-sha256-hash>

The 'add' command generates a cryptographically random key, prints it
ONCE to stdout, then stores only its SHA-256 hash. Copy it immediately.
"""

import argparse
import os
import secrets
import sys

# Ensure the project root is on the path when run directly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import key_store


def cmd_add(args: argparse.Namespace) -> None:
    key_store.init_db()

    raw_key = f"apx_{secrets.token_hex(16)}"  # e.g. apx_3f8a1b...  (36 chars total)

    try:
        key_hash = key_store.add_key(
            raw_key=raw_key,
            label=args.label,
            plan=args.plan,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print()
    print("=" * 60)
    print(" NEW API KEY — copy it now, it will NOT be shown again")
    print("=" * 60)
    print(f"  Key   : {raw_key}")
    print(f"  Hash  : {key_hash[:16]}...")
    print(f"  Label : {args.label}")
    print(f"  Plan  : {args.plan}")
    print("=" * 60)
    print()


def cmd_list(_args: argparse.Namespace) -> None:
    key_store.init_db()
    keys = key_store.list_keys()

    if not keys:
        print("No keys found.")
        return

    header = f"{'PREFIX':<12}  {'LABEL':<20}  {'PLAN':<10}  {'ACTIVE':<7}  {'REQ/DAY':<9}  {'TOK/DAY':<10}  {'TODAY REQ':<10}  {'TODAY TOK':<10}"
    print(header)
    print("-" * len(header))
    for k in keys:
        quota_req = str(k["quota_req_per_day"])  if k["quota_req_per_day"] != -1  else "∞"
        quota_tok = str(k["quota_tokens_per_day"]) if k["quota_tokens_per_day"] != -1 else "∞"
        active    = "yes" if k["is_active"] else "no"
        print(
            f"{k['key_prefix']:<12}  {k['label']:<20}  {k['plan']:<10}  {active:<7}  "
            f"{quota_req:<9}  {quota_tok:<10}  {k['today_requests']:<10}  {k['today_tokens']:<10}"
        )


def cmd_revoke(args: argparse.Namespace) -> None:
    key_store.init_db()
    revoked = key_store.revoke_key(args.hash)
    if revoked:
        print(f"Key {args.hash[:16]}... revoked successfully.")
    else:
        print(f"No active key found with hash {args.hash[:16]}...", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apex API key management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="Generate and store a new API key")
    p_add.add_argument("--label", required=True, help="Human-readable name for the key (e.g. quill-prod)")
    p_add.add_argument(
        "--plan",
        default="free",
        choices=list(key_store.PLANS),
        help="Plan tier (default: free)",
    )

    # list
    sub.add_parser("list", help="List all keys with today's usage")

    # revoke
    p_rev = sub.add_parser("revoke", help="Deactivate a key by its full SHA-256 hash")
    p_rev.add_argument("--hash", required=True, help="Full SHA-256 hash of the key to revoke")

    args = parser.parse_args()

    dispatch = {"add": cmd_add, "list": cmd_list, "revoke": cmd_revoke}
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

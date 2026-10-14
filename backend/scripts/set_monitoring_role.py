"""Set a trusted app_metadata role on one Supabase Auth user.

Run on a trusted administrator workstation. The key is requested via getpass and is
never printed or written to disk. Use the project's server-only service_role key.
"""
import argparse
from getpass import getpass
import sys
import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Set a Tynato monitoring role for a Supabase Auth user")
    parser.add_argument("user_id", help="Supabase Auth user UUID from Authentication → Users")
    parser.add_argument("role", choices=["super_admin", "technical_operator"])
    args = parser.parse_args()

    project_url = input("Monitoring Supabase project URL: ").strip().rstrip("/")
    if not project_url.startswith("https://"):
        print("Error: use the HTTPS project URL.", file=sys.stderr)
        return 2
    key = getpass("Monitoring project's service_role key (input hidden): ").strip()
    if not key:
        print("Error: key is required.", file=sys.stderr)
        return 2

    url = f"{project_url}/auth/v1/admin/users/{args.user_id}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            current = client.get(url, headers=headers)
            current.raise_for_status()
            data = current.json()
            app_metadata = data.get("app_metadata") or {}
            app_metadata["monitoring_role"] = args.role
            updated = client.put(url, headers=headers, json={"app_metadata": app_metadata})
            updated.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Role update failed ({type(exc).__name__}). Verify the project URL, user UUID and server-side key.", file=sys.stderr)
        return 1

    print(f"Role set to {args.role}. Sign out and sign in again to refresh the user's JWT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

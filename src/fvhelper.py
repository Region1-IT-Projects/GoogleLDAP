#!/usr/bin/env python3
"""Create mobile accounts for Google LDAP users so they can log in offline and unlock FileVault.

This tool is interactive on purpose -- it needs an admin password that must not live on the
USB drive. Run it by hand, after install.sh has run and the Mac has rebooted:

    sudo ./python/Versions/Current/bin/python3 fvhelper.py

Each account is created against the admin's credentials, so the admin must already hold a
SecureToken for the new accounts to be FileVault-capable.
"""

import argparse
import getpass
import json
import os
import subprocess
import sys

CREATEMOBILEACCOUNT = (
    "/System/Library/CoreServices/ManagedClient.app/Contents/Resources/createmobileaccount"
)


def run(args, stdin_text=None):
    return subprocess.run(args, capture_output=True, text=True, input=stdin_text)


def resolve_node_name():
    """Read the node name from the payload config if it's sitting next to us."""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    try:
        with open(config_path, encoding="utf-8") as handle:
            return json.load(handle).get("node_name", "/LDAPv3/ldap.google.com")
    except (OSError, ValueError):
        return "/LDAPv3/ldap.google.com"


def user_exists_in_directory(node_name, username):
    result = run(["dscl", node_name, "-read", f"/Users/{username}", "RecordName"])
    return result.returncode == 0


def has_secure_token(username):
    result = run(["sysadminctl", "-secureTokenStatus", username])
    combined = (result.stdout or "") + (result.stderr or "")
    return "ENABLED" in combined.upper()


def create_mobile_account(username, admin_user, admin_password):
    """Create the mobile account.

    The admin password is passed as an argv value because createmobileaccount offers no
    stdin option. That exposes it in `ps` for the lifetime of the call, so we keep the call
    as short-lived as possible and never write the password anywhere.
    """
    return run([
        CREATEMOBILEACCOUNT,
        "-n", username,
        "-v",
        "-a", admin_user,
        "-U", admin_password,
    ])


def main():
    parser = argparse.ArgumentParser(
        description="Create mobile accounts for Google LDAP users (FileVault-capable)."
    )
    parser.add_argument(
        "users", nargs="*",
        help="usernames to create; prompted for if omitted. An @domain suffix is stripped.",
    )
    parser.add_argument(
        "--admin", default=None,
        help="admin account whose SecureToken authorises the new accounts "
             "(default: the invoking console user)",
    )
    parser.add_argument(
        "--skip-directory-check", action="store_true",
        help="create accounts without first confirming they resolve in LDAP",
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: must run as root -- use sudo", file=sys.stderr)
        return 10

    if not os.path.exists(CREATEMOBILEACCOUNT):
        print(f"ERROR: {CREATEMOBILEACCOUNT} not found on this system", file=sys.stderr)
        return 78

    admin_user = args.admin or os.environ.get("SUDO_USER") or ""
    if not admin_user:
        admin_user = input("Admin account name: ").strip()
    if not admin_user:
        print("ERROR: an admin account name is required", file=sys.stderr)
        return 2

    usernames = list(args.users)
    if not usernames:
        print("Enter the usernames that should get mobile accounts, space separated.")
        usernames = input("> ").split()
    # Accept full email addresses but use the short name, which is what LDAP records use.
    usernames = [name.split("@")[0].strip() for name in usernames if name.strip()]
    if not usernames:
        print("Nothing to do.")
        return 0

    print(f"\nThese accounts will be created using {admin_user}'s SecureToken.")
    if not has_secure_token(admin_user):
        print(
            f"WARNING: {admin_user} does not appear to hold a SecureToken. The accounts may be\n"
            "         created but will not be able to unlock FileVault."
        )

    # getpass keeps the password off the screen and out of shell scrollback.
    admin_password = getpass.getpass(f"Password for {admin_user}: ")
    if not admin_password:
        print("ERROR: password is required", file=sys.stderr)
        return 2

    node_name = resolve_node_name()
    results = []
    for username in usernames:
        print(f"\n--> {username}")
        if not args.skip_directory_check and not user_exists_in_directory(node_name, username):
            print(f"    SKIP: not found in {node_name}")
            results.append((username, "skipped (not in directory)"))
            continue

        result = create_mobile_account(username, admin_user, admin_password)
        if result.returncode == 0:
            print("    created")
            results.append((username, "created"))
        else:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            print(f"    FAILED: {detail[-1] if detail else f'exit {result.returncode}'}")
            results.append((username, f"failed (exit {result.returncode})"))

    print("\nSummary")
    for username, status in results:
        print(f"  {username}: {status}")

    failed = sum(1 for _, status in results if status.startswith("failed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

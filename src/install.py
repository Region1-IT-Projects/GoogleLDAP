#!/usr/bin/env python3
"""Configure this Mac to authenticate against Google Workspace via Secure LDAP.

Runs unattended: no prompts, no TTY required. Launch it as `sudo ./install.sh`.
Every step is idempotent, so re-running on an already-provisioned Mac is safe.
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
import sys

SYSTEM_KEYCHAIN = "/Library/Keychains/System.keychain"
LDAP_CONF = "/etc/openldap/ldap.conf"
LOGINWINDOW_PREFS = "/Library/Preferences/com.apple.loginwindow"
OD_CONFIG_DIR = "/Library/Preferences/OpenDirectory/Configurations/LDAPv3"
LOCAL_LOG = "/var/log/googleldap-install.log"

# opendirectoryd needs access to the client identity's private key to do mutual TLS.
KEYCHAIN_TRUSTED_APPS = (
    "/usr/libexec/opendirectoryd",
    "/usr/bin/dscl",
    "/System/Library/CoreServices/Applications/Directory Utility.app",
)

# Distinct codes so a tech (or a log sweep) can tell failures apart at a glance.
EXIT_OK = 0
EXIT_NOT_ROOT = 10
EXIT_UNSUPPORTED_HW = 11
EXIT_UNSUPPORTED_OS = 12
EXIT_BAD_PAYLOAD = 13
EXIT_KEYCHAIN_FAILED = 20
EXIT_OD_WRITE_FAILED = 21
EXIT_LDAP_CONF_FAILED = 22
EXIT_SEARCH_PATH_FAILED = 23
EXIT_STATE_CHECK_FAILED = 24


class InstallError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


class Logger:
    """Writes to stdout, /var/log, and a serial-named log on the payload drive."""

    def __init__(self, payload_dir):
        self.handles = []
        self.paths = []
        serial = hardware_serial() or "unknown-serial"
        candidates = [LOCAL_LOG, os.path.join(payload_dir, "logs", f"{serial}.log")]
        for path in candidates:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                handle = open(path, "a", encoding="utf-8")
            except OSError:
                continue  # a read-only drive or unwritable /var/log must not abort the install
            self.handles.append(handle)
            self.paths.append(path)
        self.write(f"\n===== googleldap install {datetime.datetime.now().isoformat()} =====")
        self.write(f"host serial: {serial}")

    def write(self, message):
        print(message, flush=True)
        for handle in self.handles:
            handle.write(message + "\n")
            handle.flush()

    def step(self, message):
        self.write(f"--> {message}")

    def close(self):
        for handle in self.handles:
            handle.close()
        self.handles = []


def run(args, check=True, capture=True, stdin_text=None):
    result = subprocess.run(
        args,
        capture_output=capture,
        text=True,
        input=stdin_text,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout, stderr=detail
        )
    return result


def hardware_serial():
    try:
        out = run(
            ["ioreg", "-c", "IOPlatformExpertDevice", "-d", "2"], check=False
        ).stdout
    except OSError:
        return None
    match = re.search(r'"IOPlatformSerialNumber"\s*=\s*"([^"]+)"', out or "")
    return match.group(1) if match else None


# --------------------------------------------------------------------- preflight


def check_root():
    if os.geteuid() != 0:
        raise InstallError("must run as root -- use: sudo ./install.sh", EXIT_NOT_ROOT)


def check_hardware():
    """Apple Silicon only. platform.machine() lies under Rosetta, so ask the kernel."""
    translated = run(
        ["sysctl", "-n", "sysctl.proc_translated"], check=False
    ).stdout.strip()
    if translated == "1":
        raise InstallError(
            "running under Rosetta translation; run the native interpreter instead",
            EXIT_UNSUPPORTED_HW,
        )
    arch = run(["uname", "-m"], check=False).stdout.strip()
    if arch != "arm64":
        raise InstallError(
            f"this installer supports Apple Silicon only (detected {arch or 'unknown'})",
            EXIT_UNSUPPORTED_HW,
        )
    return arch


def check_os(min_major):
    # sw_vers rather than platform.mac_ver(), which can report "10.16" under the
    # SYSTEM_VERSION_COMPAT shim depending on how the interpreter was built.
    version = run(["sw_vers", "-productVersion"], check=False).stdout.strip()
    if not version:
        version = platform.mac_ver()[0]
    if not version:
        raise InstallError("could not determine the macOS version", EXIT_UNSUPPORTED_OS)
    try:
        major = int(version.split(".")[0])
    except ValueError:
        raise InstallError(f"unparseable macOS version: {version}", EXIT_UNSUPPORTED_OS)
    if major < min_major:
        raise InstallError(
            f"macOS {version} is older than the supported minimum ({min_major})",
            EXIT_UNSUPPORTED_OS,
        )
    return version


def verify_manifest(payload_dir):
    """Confirm the payload wasn't corrupted in transit. Covers every file, recursively."""
    manifest = os.path.join(payload_dir, "manifest.sha256")
    if not os.path.isfile(manifest):
        raise InstallError(f"missing {manifest}", EXIT_BAD_PAYLOAD)

    checked = 0
    with open(manifest, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            expected, _, relative = line.partition("  ")
            if not relative:
                raise InstallError(f"malformed manifest line: {line}", EXIT_BAD_PAYLOAD)
            target = os.path.join(payload_dir, relative)
            if not os.path.isfile(target):
                raise InstallError(f"payload file missing: {relative}", EXIT_BAD_PAYLOAD)
            digest = hashlib.sha256()
            with open(target, "rb") as payload_file:
                for chunk in iter(lambda: payload_file.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected:
                raise InstallError(
                    f"checksum mismatch for {relative} -- payload is corrupt or modified",
                    EXIT_BAD_PAYLOAD,
                )
            checked += 1
    return checked


def load_config(payload_dir):
    path = os.path.join(payload_dir, "config.json")
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as exc:
        raise InstallError(f"could not read {path}: {exc}", EXIT_BAD_PAYLOAD)

    required = (
        "search_base",
        "node_name",
        "tls_identity",
        "login_banner",
        "reboot",
        "min_macos_major",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise InstallError(
            f"config.json missing keys: {', '.join(missing)}", EXIT_BAD_PAYLOAD
        )
    return config


def load_p12_password(payload_dir):
    path = os.path.join(payload_dir, ".env")
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError as exc:
        raise InstallError(f"could not read {path}: {exc}", EXIT_BAD_PAYLOAD)

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key.strip() == "P12_PASSWORD":
            password = value.strip().strip('"').strip("'")
            if password:
                return password
    raise InstallError(f"P12_PASSWORD not set in {path}", EXIT_BAD_PAYLOAD)


# --------------------------------------------------------------------- steps


def identity_present(tls_identity):
    result = run(["security", "find-identity", "-v", SYSTEM_KEYCHAIN], check=False)
    return tls_identity in (result.stdout or "")


def import_identity(log, p12_path, password, tls_identity, dry_run):
    if identity_present(tls_identity):
        log.write(f"    identity '{tls_identity}' already in the System keychain; skipping")
        return

    args = [
        "security", "import", p12_path,
        "-k", SYSTEM_KEYCHAIN,
        # Explicit format is required: macOS 26 misdetects PKCS#12 when left to guess.
        "-f", "pkcs12",
        "-P", password,
        # Non-extractable: opendirectoryd can use the key for TLS, but it can't be exported
        # back off the machine.
        "-x",
    ]
    for app in KEYCHAIN_TRUSTED_APPS:
        args += ["-T", app]

    if dry_run:
        log.write(f"    [dry-run] would import {p12_path}")
        return

    try:
        run(args)
    except subprocess.CalledProcessError as exc:
        raise InstallError(
            f"'security import' failed: {exc.stderr}\n"
            "  If this reports a wrong password, the payload's .env and client.p12 disagree; "
            "rebuild with deploy.sh.",
            EXIT_KEYCHAIN_FAILED,
        )

    if not identity_present(tls_identity):
        raise InstallError(
            f"import reported success but no identity named '{tls_identity}' is present",
            EXIT_KEYCHAIN_FAILED,
        )
    log.write(f"    imported '{tls_identity}' (non-extractable)")


def write_od_config(log, plist_path, node_name, dry_run):
    """Hand the LDAPv3 config to opendirectoryd via the OpenDirectory /Configure node.

    Custom call 99991 is what Google's own macOS instructions use. It's undocumented, so
    unlike the original script we check every return value and then independently confirm
    the config actually landed.
    """
    if dry_run:
        log.write(f"    [dry-run] would write {plist_path} via custom call 99991")
        return

    from Foundation import NSData, NSMutableData
    from OpenDirectory import ODNode, ODSession, kODNodeTypeConfigure

    with open(plist_path, "rb") as handle:
        config_bytes = handle.read()

    session = ODSession.defaultSession()
    if session is None:
        raise InstallError("could not get an OpenDirectory session", EXIT_OD_WRITE_FAILED)

    node, error = ODNode.nodeWithSession_type_error_(session, kODNodeTypeConfigure, None)
    if node is None or error is not None:
        raise InstallError(
            f"could not open the OpenDirectory /Configure node: {error}",
            EXIT_OD_WRITE_FAILED,
        )

    # Payload layout: a 32-byte zeroed serialized authorization reference, then the raw plist.
    request = NSMutableData.dataWithBytes_length_(b"\x00" * 32, 32)
    request.appendData_(NSData.dataWithBytes_length_(config_bytes, len(config_bytes)))

    response, error = node.customCall_sendData_error_(99991, request, None)
    if error is not None:
        raise InstallError(
            f"OpenDirectory custom call 99991 failed: {error}",
            EXIT_OD_WRITE_FAILED,
        )

    node_leaf = node_name.rsplit("/", 1)[-1]
    expected = os.path.join(OD_CONFIG_DIR, f"{node_leaf}.plist")
    if not os.path.isfile(expected):
        raise InstallError(
            "custom call 99991 returned success but no config appeared at\n"
            f"  {expected}\n"
            "  This is the undocumented Apple API this tool depends on. If it has changed in "
            "this macOS release, the supported replacement is "
            "ODSession.addConfiguration:authorization:error:.",
            EXIT_OD_WRITE_FAILED,
        )
    log.write(f"    config written and confirmed at {expected}")


def set_tls_identity(log, tls_identity, dry_run):
    """Point OpenLDAP at the keychain identity. Rewrites in place rather than appending."""
    directive = f"TLS_IDENTITY\t{tls_identity}"
    try:
        with open(LDAP_CONF, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as exc:
        raise InstallError(f"could not read {LDAP_CONF}: {exc}", EXIT_LDAP_CONF_FAILED)

    kept = [line for line in lines if not line.strip().startswith("TLS_IDENTITY")]
    removed = len(lines) - len(kept)
    kept.append(directive)

    if dry_run:
        log.write(f"    [dry-run] would set {directive!r} in {LDAP_CONF}")
        return

    try:
        with open(LDAP_CONF, "w", encoding="utf-8") as handle:
            handle.write("\n".join(kept) + "\n")
    except OSError as exc:
        raise InstallError(f"could not write {LDAP_CONF}: {exc}", EXIT_LDAP_CONF_FAILED)

    if removed:
        log.write(f"    replaced {removed} existing TLS_IDENTITY line(s)")
    log.write(f"    TLS_IDENTITY set to '{tls_identity}'")


def current_search_path():
    result = run(
        ["dscl", "-q", "localhost", "-read", "/Search", "CSPSearchPath"], check=False
    )
    return result.stdout or ""


def configure_search_path(log, node_name, dry_run):
    """Add the LDAP node to the custom search path and select that policy.

    dscl is used deliberately: it goes through opendirectoryd, whereas writing Search.plist
    directly is blocked by SIP.
    """
    if dry_run:
        log.write(f"    [dry-run] would add {node_name} to CSPSearchPath")
        return

    try:
        run(["dscl", "-q", "localhost", "-create", "/Search", "SearchPolicy", "CSPSearchPath"])
    except subprocess.CalledProcessError as exc:
        raise InstallError(
            f"could not set the search policy to custom: {exc.stderr}",
            EXIT_SEARCH_PATH_FAILED,
        )

    if node_name in current_search_path():
        log.write(f"    {node_name} already in CSPSearchPath; skipping")
        return

    try:
        run(["dscl", "-q", "localhost", "-append", "/Search", "CSPSearchPath", node_name])
    except subprocess.CalledProcessError as exc:
        raise InstallError(
            f"could not add {node_name} to the search path: {exc.stderr}\n"
            "  This usually means the directory config above did not apply.",
            EXIT_SEARCH_PATH_FAILED,
        )
    log.write(f"    added {node_name} to CSPSearchPath")


def set_loginwindow(log, banner, dry_run):
    settings = [
        ("SHOWFULLNAME", "-bool", "TRUE"),
        ("LoginwindowText", "-string", banner),
    ]
    for key, kind, value in settings:
        if dry_run:
            log.write(f"    [dry-run] would set {key}")
            continue
        try:
            run(["defaults", "write", LOGINWINDOW_PREFS, key, kind, value])
        except subprocess.CalledProcessError as exc:
            # Cosmetic settings shouldn't fail an otherwise good provisioning run.
            log.write(f"    WARNING: could not set {key}: {exc.stderr}")
    log.write("    login window configured")


def confirm_state(log, config, dry_run):
    """Confirm what we wrote is actually on disk.

    Deliberately local-only: the directory binding does not become active until the reboot,
    so a live lookup here would report a false failure. The certificate itself was already
    proven against Google by deploy.sh at build time.
    """
    if dry_run:
        log.write("    [dry-run] skipping state confirmation")
        return

    problems = []

    if not identity_present(config["tls_identity"]):
        problems.append("client identity is not in the System keychain")

    node_leaf = config["node_name"].rsplit("/", 1)[-1]
    if not os.path.isfile(os.path.join(OD_CONFIG_DIR, f"{node_leaf}.plist")):
        problems.append("OpenDirectory node config is missing")

    try:
        with open(LDAP_CONF, encoding="utf-8") as handle:
            identity_lines = [
                line for line in handle.read().splitlines()
                if line.strip().startswith("TLS_IDENTITY")
            ]
    except OSError:
        identity_lines = []
    if len(identity_lines) != 1:
        problems.append(f"expected exactly 1 TLS_IDENTITY line, found {len(identity_lines)}")

    if config["node_name"] not in current_search_path():
        problems.append("LDAP node is not in the search path")

    if problems:
        raise InstallError(
            "post-install checks failed:\n" + "\n".join(f"  - {p}" for p in problems),
            EXIT_STATE_CHECK_FAILED,
        )
    log.write("    all local state confirmed")


# --------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(
        description="Provision this Mac for Google Secure LDAP authentication."
    )
    parser.add_argument(
        "--no-reboot", action="store_true",
        help="don't reboot when finished (the binding stays inactive until a reboot)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="run the checks and report intended actions without changing anything",
    )
    args = parser.parse_args()

    payload_dir = os.path.dirname(os.path.abspath(__file__))
    log = None
    try:
        check_root()
        log = Logger(payload_dir)
        log.write(f"payload: {payload_dir}")
        if args.dry_run:
            log.write("DRY RUN -- no changes will be made")

        log.step("Preflight")
        arch = check_hardware()
        config = load_config(payload_dir)
        os_version = check_os(int(config["min_macos_major"]))
        log.write(f"    macOS {os_version} on {arch}")
        file_count = verify_manifest(payload_dir)
        log.write(f"    payload integrity verified ({file_count} files)")
        password = load_p12_password(payload_dir)
        log.write(f"    target domain: {config['search_base']}")
        if config.get("cert_not_after"):
            log.write(f"    certificate expires: {config['cert_not_after']}")

        log.step("Importing client identity into the System keychain")
        import_identity(
            log,
            os.path.join(payload_dir, "assets", "client.p12"),
            password,
            config["tls_identity"],
            args.dry_run,
        )

        log.step("Writing the OpenDirectory LDAP configuration")
        write_od_config(
            log,
            os.path.join(payload_dir, "assets", "ldap.google.com.plist"),
            config["node_name"],
            args.dry_run,
        )

        log.step("Configuring the OpenLDAP TLS identity")
        set_tls_identity(log, config["tls_identity"], args.dry_run)

        log.step("Adding the directory node to the search path")
        configure_search_path(log, config["node_name"], args.dry_run)

        log.step("Setting login window preferences")
        set_loginwindow(log, config["login_banner"], args.dry_run)

        log.step("Confirming installed state")
        confirm_state(log, config, args.dry_run)

    except InstallError as exc:
        message = f"FAIL: {exc}"
        if log:
            log.write(message)
            log.close()
        else:
            print(message, file=sys.stderr, flush=True)
        return exc.code
    except Exception as exc:  # noqa: BLE001 - unattended runs must log, not traceback silently
        message = f"FAIL: unexpected error: {exc.__class__.__name__}: {exc}"
        if log:
            log.write(message)
            log.close()
        else:
            print(message, file=sys.stderr, flush=True)
        return 1

    should_reboot = bool(config.get("reboot", True)) and not args.no_reboot and not args.dry_run

    log.write("")
    log.write("PASS: Google Secure LDAP configured successfully.")
    if log.paths:
        log.write(f"logs: {', '.join(log.paths)}")
    if not should_reboot:
        log.write("NOTE: a reboot is required before directory logins will work.")
    log.close()

    if should_reboot:
        print("Rebooting now...", flush=True)
        subprocess.run(["shutdown", "-r", "now"], check=False)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

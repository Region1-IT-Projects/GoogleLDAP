#!/bin/zsh
# Builds a self-contained Google Secure LDAP provisioning payload for USB deployment.
# Run this on a macOS admin machine, then copy the output directory to a flash drive.

set -u
set -o pipefail

readonly SCRIPT_DIR="${0:A:h}"
readonly SRC_DIR="$SCRIPT_DIR/src"
readonly TEMPLATE="$SRC_DIR/ldap.google.com.plist.in"
readonly RELOCATABLE_PYTHON_DIR="$SCRIPT_DIR/vendor/relocatable-python"

readonly DEFAULT_SEARCH_BASE="dc=region1schools,dc=org"
readonly LDAP_HOST="ldap.google.com"
readonly LDAP_PORT="636"
readonly NODE_NAME="/LDAPv3/ldap.google.com"
readonly MIN_MACOS_MAJOR="15"
readonly DEFAULT_BANNER="By logging on to this system you are agreeing to abide by the terms of the Region 1 acceptable use policy."

# Python bundled into the payload so target Macs need no runtime installed.
readonly PYTHON_VERSION="3.12.8"
readonly CACHE_DIR="$HOME/.cache/googleldap-deploy"

CERT_PATH=""
KEY_PATH=""
OUT_DIR=""
SEARCH_BASE="$DEFAULT_SEARCH_BASE"
BANNER="$DEFAULT_BANNER"
REBOOT_DEFAULT="true"
SKIP_HANDSHAKE="false"
ASSUME_YES="false"

die() { print -ru2 -- "ERROR: $*"; exit 1; }
warn() { print -ru2 -- "WARNING: $*"; }
info() { print -r -- "$*"; }
step() { print -r -- ""; print -r -- "==> $*"; }

usage() {
    cat <<'EOF'
Usage: ./deploy.sh [options]

Builds a USB payload that provisions a Mac for Google Secure LDAP.

Options:
  --cert PATH           Google-issued .crt file
  --key PATH            Google-issued .key file
  --out DIR             Output directory for the payload
  --search-base DN      LDAP search base (default: dc=region1schools,dc=org)
  --banner TEXT         Login window banner text
  --no-reboot-default   Payload defaults to not rebooting after install
  --skip-handshake      Skip the live mTLS check against Google (offline builds)
  --yes                 Don't prompt for anything; fail if a value is missing
  -h, --help            Show this help

Any value not supplied on the command line is prompted for.
EOF
}

while (( $# )); do
    case "$1" in
        --cert) CERT_PATH="${2:-}"; shift 2 ;;
        --key) KEY_PATH="${2:-}"; shift 2 ;;
        --out) OUT_DIR="${2:-}"; shift 2 ;;
        --search-base) SEARCH_BASE="${2:-}"; shift 2 ;;
        --banner) BANNER="${2:-}"; shift 2 ;;
        --no-reboot-default) REBOOT_DEFAULT="false"; shift ;;
        --skip-handshake) SKIP_HANDSHAKE="true"; shift ;;
        --yes) ASSUME_YES="true"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1 (try --help)" ;;
    esac
done

json_escape() {
    # config.json is hand-built via heredoc; escape values that come from admin input or a
    # certificate's CN so a stray quote/backslash can't produce invalid or injected JSON.
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    print -r -- "$s"
}

expand_leading_tilde() {
    # ${~var} only enables glob-pattern interpretation, not tilde expansion, and only
    # outside quotes -- which makes it unsafe for arbitrary paths (glob metacharacters like
    # [, ], (, ) would be filename-generated). Handle only the common "~" and "~/..." forms
    # for the current user, as plain string manipulation.
    local path="$1"
    case "$path" in
        "~") print -r -- "$HOME" ;;
        "~/"*) print -r -- "$HOME/${path#\~/}" ;;
        *) print -r -- "$path" ;;
    esac
}

prompt_for() {
    # prompt_for <varname> <prompt text> [default]
    local varname="$1" text="$2" default="${3:-}" answer=""
    if [[ -n "${(P)varname}" ]]; then
        return 0
    fi
    if [[ "$ASSUME_YES" == "true" ]]; then
        [[ -n "$default" ]] || die "--yes given but $varname was not supplied"
        typeset -g "$varname"="$default"
        return 0
    fi
    if [[ -n "$default" ]]; then
        read -r "answer?$text [$default]: "
        [[ -n "$answer" ]] || answer="$default"
    else
        read -r "answer?$text: "
    fi
    [[ -n "$answer" ]] || die "$text is required"
    typeset -g "$varname"="$answer"
}

# ---------------------------------------------------------------- preflight

step "Preflight"

[[ "$(uname -s)" == "Darwin" ]] || die "deploy.sh must run on macOS (it uses the 'security' tool to self-test the p12)"
[[ -f "$TEMPLATE" ]] || die "missing plist template: $TEMPLATE"
[[ -x "$RELOCATABLE_PYTHON_DIR/make_relocatable_python_framework.py" ]] \
    || die "missing vendored relocatable-python: $RELOCATABLE_PYTHON_DIR"

for tool in openssl security uuidgen shasum curl plutil xattr sw_vers; do
    command -v "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done

info "macOS $(sw_vers -productVersion), $(openssl version)"

# ---------------------------------------------------------------- inputs

step "Inputs"

prompt_for CERT_PATH "Path to the Google-issued .crt"
prompt_for KEY_PATH "Path to the Google-issued .key"
prompt_for OUT_DIR "Output directory for the payload" "$PWD/payload"

CERT_PATH=$(expand_leading_tilde "$CERT_PATH")
KEY_PATH=$(expand_leading_tilde "$KEY_PATH")
OUT_DIR=$(expand_leading_tilde "$OUT_DIR")

[[ -f "$CERT_PATH" ]] || die "certificate not found: $CERT_PATH"
[[ -f "$KEY_PATH" ]] || die "key not found: $KEY_PATH"

# ---------------------------------------------------------------- validate credential

step "Validating certificate and key"

openssl x509 -in "$CERT_PATH" -noout >/dev/null 2>&1 || die "$CERT_PATH is not a readable X.509 certificate"

cert_subject=$(openssl x509 -in "$CERT_PATH" -noout -subject | sed 's/^subject=//')
cert_serial=$(openssl x509 -in "$CERT_PATH" -noout -serial | sed 's/^serial=//')
cert_not_after=$(openssl x509 -in "$CERT_PATH" -noout -enddate | sed 's/^notAfter=//')

info "Subject : $cert_subject"
info "Serial  : $cert_serial"
info "Expires : $cert_not_after"

# The cert's CN becomes the TLS_IDENTITY value; Google requires an exact match.
TLS_IDENTITY=$(openssl x509 -in "$CERT_PATH" -noout -subject -nameopt multiline \
    | awk -F' = ' '/commonName/ {print $2}')
[[ -n "$TLS_IDENTITY" ]] || die "could not read a commonName (CN) from the certificate; TLS_IDENTITY needs it"
info "TLS_IDENTITY will be: $TLS_IDENTITY"

if ! openssl x509 -in "$CERT_PATH" -noout -checkend 0 >/dev/null 2>&1; then
    die "certificate expired on $cert_not_after -- generate a new one in Google Admin (Apps > LDAP > your client > Authentication)"
fi
if ! openssl x509 -in "$CERT_PATH" -noout -checkend 2592000 >/dev/null 2>&1; then
    warn "certificate expires within 30 days ($cert_not_after)"
fi

# A cert/key mismatch otherwise surfaces as a confusing keychain or TLS error later.
cert_pub=$(openssl x509 -in "$CERT_PATH" -noout -pubkey 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
key_pub=$(openssl pkey -in "$KEY_PATH" -pubout -outform DER 2>/dev/null | shasum -a 256 | cut -d' ' -f1)
[[ -n "$cert_pub" && -n "$key_pub" ]] || die "could not extract public keys for comparison (is $KEY_PATH a valid private key?)"
[[ "$cert_pub" == "$key_pub" ]] || die "the key does not match the certificate -- they are not a pair"
info "Key matches certificate."

# ---------------------------------------------------------------- live handshake

step "Checking the credential against Google"

if [[ "$SKIP_HANDSHAKE" == "true" ]]; then
    warn "skipping mTLS handshake (--skip-handshake); the credential is unverified"
else
    handshake_log=$(mktemp -t googleldap-handshake)
    if print -r -- "" | openssl s_client -connect "${LDAP_HOST}:${LDAP_PORT}" \
        -cert "$CERT_PATH" -key "$KEY_PATH" -servername "$LDAP_HOST" \
        >"$handshake_log" 2>&1; then
        if grep -q "Verify return code: 0 (ok)" "$handshake_log"; then
            info "mTLS handshake with ${LDAP_HOST}:${LDAP_PORT} succeeded; Google accepted this certificate."
        else
            warn "handshake completed but server verification was not clean:"
            grep -E "Verify return code|verify error" "$handshake_log" | sed 's/^/    /'
        fi
    else
        print -ru2 -- "--- handshake output ---"
        tail -n 20 "$handshake_log" >&2
        rm -f "$handshake_log"
        die "could not complete an mTLS handshake with ${LDAP_HOST}:${LDAP_PORT}.
  Common causes: the certificate was deleted/revoked in Google Admin, the LDAP client's
  access permissions are not configured, or there is no network route to Google.
  Use --skip-handshake to build anyway."
    fi
    rm -f "$handshake_log"
fi

# ---------------------------------------------------------------- build payload

payload_name="GoogleLDAP-$(print -r -- "$SEARCH_BASE" | sed -E 's/dc=//; s/,dc=.*//')"
PAYLOAD="$OUT_DIR/$payload_name"

step "Building payload at $PAYLOAD"

if [[ -e "$PAYLOAD" ]]; then
    if [[ "$ASSUME_YES" != "true" ]]; then
        confirm=""
        read -r "confirm?$PAYLOAD already exists. Replace it? [y/N]: "
        [[ "$confirm" == [yY]* ]] || die "aborted"
    fi
    rm -rf "$PAYLOAD"
fi

mkdir -p "$PAYLOAD/assets" "$PAYLOAD/logs" || die "could not create $PAYLOAD"

# ---------------------------------------------------------------- p12

step "Generating client.p12"

# PBE-SHA1-3DES + SHA-1 MAC is what macOS's Security framework reliably imports. macOS <= 14
# cannot read OpenSSL 3's modern defaults (AES-256-CBC/PBKDF2/SHA-256) at all, and 3DES avoids
# needing OpenSSL 3's legacy provider (unlike the RC2-40 that Keychain Access exports use).
P12_PASSWORD=$(openssl rand -base64 30 | tr -d '\n')
[[ -n "$P12_PASSWORD" ]] || die "failed to generate a p12 password"
# Passed via the environment rather than argv so it never appears in `ps`.
export P12_PASSWORD

p12_path="$PAYLOAD/assets/client.p12"
openssl pkcs12 -export \
    -in "$CERT_PATH" -inkey "$KEY_PATH" \
    -out "$p12_path" \
    -name "$TLS_IDENTITY" \
    -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 \
    -passout env:P12_PASSWORD \
    || die "openssl pkcs12 -export failed"
chmod 600 "$p12_path"
info "Wrote $(basename "$p12_path") ($(stat -f%z "$p12_path") bytes)"

step "Self-testing the p12 with the real 'security import'"

# Importing into a throwaway keychain here means a PKCS#12 incompatibility can never be
# discovered halfway through provisioning a room full of Macs.
test_keychain=$(mktemp -u "/tmp/googleldap-selftest-XXXXXX.keychain")
test_kc_pass=$(openssl rand -base64 18 | tr -d '\n')
cleanup_test_keychain() {
    security delete-keychain "$test_keychain" >/dev/null 2>&1 || true
    rm -f "$test_keychain" >/dev/null 2>&1 || true
}
trap cleanup_test_keychain EXIT INT TERM

security create-keychain -p "$test_kc_pass" "$test_keychain" \
    || die "could not create a temporary keychain for the self-test"
security unlock-keychain -p "$test_kc_pass" "$test_keychain" >/dev/null 2>&1

# -f pkcs12 is mandatory, not cosmetic: macOS 26's format auto-detection misidentifies p12 files.
if ! security import "$p12_path" -k "$test_keychain" -f pkcs12 -P "$P12_PASSWORD" -A >/dev/null 2>&1; then
    cleanup_test_keychain
    die "the generated p12 could not be imported by macOS 'security import'.
  This is the check that protects the whole deployment batch -- do not ship this payload."
fi

if security find-identity -v "$test_keychain" 2>/dev/null | grep -qF -- "$TLS_IDENTITY"; then
    info "Self-test passed: identity '$TLS_IDENTITY' imports cleanly."
else
    warn "p12 imported but no identity named '$TLS_IDENTITY' was listed; check the certificate CN."
fi
cleanup_test_keychain
trap - EXIT INT TERM

# ---------------------------------------------------------------- plist

step "Rendering ldap.google.com.plist"

config_uuid=$(uuidgen)
plist_path="$PAYLOAD/assets/ldap.google.com.plist"
sed -e "s|__SEARCH_BASE__|$SEARCH_BASE|g" \
    -e "s|__CONFIG_UUID__|$config_uuid|g" \
    "$TEMPLATE" > "$plist_path" || die "failed to render the plist template"

grep -q "__SEARCH_BASE__\|__CONFIG_UUID__" "$plist_path" \
    && die "unsubstituted placeholders remain in $plist_path"
plutil -lint "$plist_path" >/dev/null || die "rendered plist is not valid: $plist_path"
info "Search base : $SEARCH_BASE"
info "Config UUID : $config_uuid"

# ---------------------------------------------------------------- scripts + config

step "Copying installer"

cp "$SRC_DIR/install.py" "$SRC_DIR/install.sh" "$SRC_DIR/fvhelper.py" "$PAYLOAD/" \
    || die "could not copy installer scripts from $SRC_DIR"
chmod 755 "$PAYLOAD/install.sh"

cat > "$PAYLOAD/config.json" <<EOF
{
  "search_base": "$(json_escape "$SEARCH_BASE")",
  "node_name": "$(json_escape "$NODE_NAME")",
  "tls_identity": "$(json_escape "$TLS_IDENTITY")",
  "login_banner": "$(json_escape "$BANNER")",
  "reboot": $REBOOT_DEFAULT,
  "min_macos_major": $MIN_MACOS_MAJOR,
  "cert_serial": "$(json_escape "$cert_serial")",
  "cert_not_after": "$(json_escape "$cert_not_after")",
  "config_uuid": "$(json_escape "$config_uuid")",
  "built": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
plutil -lint "$PAYLOAD/config.json" >/dev/null || die "generated config.json is not valid JSON"

umask 077
print -r -- "P12_PASSWORD=$P12_PASSWORD" > "$PAYLOAD/.env"
chmod 600 "$PAYLOAD/.env"
umask 022
info "Wrote config.json and .env (.env is mode 600)"

# ---------------------------------------------------------------- python

step "Bundling Python + pyobjc"

mkdir -p "$CACHE_DIR"

requirements="$CACHE_DIR/requirements.txt"
cat > "$requirements" <<'EOF'
pyobjc-core
pyobjc-framework-Cocoa
pyobjc-framework-OpenDirectory
EOF

framework_dir="$PAYLOAD/python"
info "Building Python $PYTHON_VERSION framework (this takes a few minutes)..."
# relocatable-python is vendored under vendor/ rather than git-cloned, so this build needs no
# git on the admin machine and is pinned to a known-good version -- see vendor/.../VENDORED.md.
# It still needs network on first build to download the actual python.org installer package.
( cd "$RELOCATABLE_PYTHON_DIR" && ./make_relocatable_python_framework.py \
    --python-version "$PYTHON_VERSION" \
    --os-version 11 \
    --pip-requirements "$requirements" \
    --destination "$PAYLOAD" ) \
    || die "relocatable-python build failed"

# relocatable-python emits Python.framework; normalise the name the installer looks for.
if [[ -d "$PAYLOAD/Python.framework" ]]; then
    mv "$PAYLOAD/Python.framework" "$framework_dir"
fi
[[ -d "$framework_dir" ]] || die "expected a bundled framework at $framework_dir"

bundled_python="$framework_dir/Versions/Current/bin/python3"
[[ -x "$bundled_python" ]] || die "bundled interpreter not executable: $bundled_python"

# Files that arrived via a browser/curl carry com.apple.quarantine, which would make the
# interpreter refuse to run on a target Mac.
xattr -dr com.apple.quarantine "$framework_dir" >/dev/null 2>&1 || true

step "Smoke-testing the bundled interpreter"
"$bundled_python" -c "import OpenDirectory, Foundation; print('OpenDirectory bindings OK')" \
    || die "the bundled Python cannot import OpenDirectory -- the payload would fail on every Mac"

# ---------------------------------------------------------------- manifest

step "Writing manifest.sha256"

manifest="$PAYLOAD/manifest.sha256"
( cd "$PAYLOAD" && find . -type f \
    ! -name "manifest.sha256" \
    ! -path "./logs/*" \
    -print0 | xargs -0 shasum -a 256 > "manifest.sha256" ) \
    || die "could not build the manifest"
info "Hashed $(wc -l < "$manifest" | tr -d ' ') files."

# ---------------------------------------------------------------- summary

cat <<EOF

================================================================
Payload ready: $PAYLOAD

  Search base   : $SEARCH_BASE
  TLS identity  : $TLS_IDENTITY
  Cert serial   : $cert_serial
  Cert expires  : $cert_not_after
  Reboot default: $REBOOT_DEFAULT

Next steps
  1. Copy the whole '$payload_name' directory to the flash drive:
       cp -R "$PAYLOAD" /Volumes/YOUR_DRIVE/
  2. On each target Mac, from the drive:
       cd /Volumes/YOUR_DRIVE/$payload_name && sudo ./install.sh
     It runs unattended and reboots when done. Per-machine logs land in ./logs/.

Reminders
  * The .env holds the p12 password and sits beside the p12 -- the drive is the
    security boundary, not the password. Keep physical custody of it.
  * To revoke access, DELETE the certificate in Google Admin (Apps > LDAP > your
    client > Authentication). Turning Service Status off can take up to 24 hours.
  * Rebuild the payload before $cert_not_after.
================================================================
EOF

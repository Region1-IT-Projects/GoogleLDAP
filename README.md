# macOS Google Secure LDAP Provisioning

Configures Apple Silicon Macs to authenticate users against the Region 1 Google Workspace
domain (`region1schools.org`) using Google's Secure LDAP service.

The tool is built for **bulk deployment from a USB flash drive**. There are two halves:

| | Where it runs | Interactive? |
|---|---|---|
| `deploy.sh` | Your macOS admin machine, once per certificate | Yes, by design |
| `install.sh` | Each target Mac, from the flash drive | No — the sudo password is the only input |

`deploy.sh` takes the certificate and key that Google issues you, validates them, and builds a
self-contained payload directory. You copy that directory to a flash drive. A tech then runs
`sudo ./install.sh` on each Mac; it provisions the machine unattended and reboots.

---

## Requirements

- **Targets:** Apple Silicon only, macOS 15 or newer. Intel is not supported.
- **Admin machine:** macOS, with network access on the first build (it downloads a Python
  framework from python.org). The tool that builds it is vendored in this repo, so no `git` or
  other extra tooling is required beyond what macOS ships.
- **Google Workspace:** Enterprise, Education, or Cloud Identity Premium — Secure LDAP is not
  available on other tiers.

---

## One-time Google Workspace setup

In the Google Admin console, under **Apps → LDAP**:

1. **Add an LDAP client** (or select the existing one).
2. **Configure access permissions.** Three toggles, each scopeable to the whole domain or to
   specific OUs/groups:
   - *Verify user credentials* — which accounts may authenticate.
   - *Read user information* — which accounts' attributes may be read.
   - *Read group information* — group details and membership.

   > **This is the most common cause of a silent failure.** macOS looks a user up during
   > authentication, so **"Read user information" must be enabled for every OU where "Verify user
   > credentials" is enabled.** If it isn't, logins misbehave with no useful error.
3. **Generate a certificate** under the client's *Authentication* card. You get a `.zip`
   containing a `.crt` and a `.key`. Keep both; `deploy.sh` needs them.

### Two limitations worth knowing before you deploy

- A user's Workspace username **must differ from their local macOS profile's user ID**, or
  sign-in is blocked. This is a Google-documented constraint, not a bug in this tool.
- **Internet access is required for a user's first login.** Offline logins afterward require a
  mobile account — see [Mobile accounts and FileVault](#mobile-accounts-and-filevault).

---

## Building a payload

On your admin Mac:

```sh
./deploy.sh
```

It prompts for the certificate path, key path, and output directory. Or pass them directly:

```sh
./deploy.sh --cert ~/Downloads/ldap-client.crt \
            --key  ~/Downloads/ldap-client.key \
            --out  ~/ldap-payload
```

Useful flags: `--search-base DN`, `--banner TEXT`, `--no-reboot-default`, `--skip-handshake`
(offline builds), `--yes` (no prompts), `--help`.

`deploy.sh` will refuse to build if:

- the certificate has **expired** (or warns if it expires within 30 days),
- the key and certificate **aren't a pair**,
- Google **rejects the credential** — it completes a real mTLS handshake against
  `ldap.google.com:636` to confirm the cert works before you provision anything,
- the generated `.p12` **can't be imported** by macOS's own `security import`.

That last check matters: it runs the real import against a throwaway keychain, so a PKCS#12
incompatibility surfaces at your desk rather than halfway through a cart of Macs.

Then copy the payload to the drive:

```sh
cp -R ~/ldap-payload/GoogleLDAP-region1schools /Volumes/YOUR_DRIVE/
```

---

## Provisioning a Mac

From the flash drive on the target machine:

```sh
cd /Volumes/YOUR_DRIVE/GoogleLDAP-region1schools
sudo ./install.sh
```

It verifies the payload, configures the machine, prints `PASS` or `FAIL`, and reboots. **The
reboot is what activates the directory binding** — `opendirectoryd` can no longer be reliably
restarted in place (Apple blocked `launchctl kickstart -k` for system processes in macOS 14.4),
so there is no way to skip it.

Flags: `--no-reboot` (leaves the binding inactive until you reboot manually), `--dry-run`
(checks and reports without changing anything).

Re-running on an already-provisioned Mac is safe — every step is idempotent.

### What it does

1. Verifies payload integrity (SHA-256 manifest over every file).
2. Imports the client identity into the **System** keychain, non-extractable, granting access to
   `opendirectoryd`, `dscl`, and Directory Utility.
3. Writes the LDAPv3 configuration into OpenDirectory, then confirms it landed.
4. Sets `TLS_IDENTITY` in `/etc/openldap/ldap.conf`.
5. Adds the directory node to the custom search path.
6. Sets the login window to show full name fields plus the acceptable-use banner.
7. Confirms all of the above, logs the result, and reboots.

### Logs

Written to `/var/log/googleldap-install.log` on the Mac **and** to `logs/<serial>.log` on the
flash drive — so after a batch you can review every machine's outcome from the drive itself.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 10 | Not run as root |
| 11 | Not Apple Silicon (or running under Rosetta) |
| 12 | macOS too old |
| 13 | Payload missing, corrupt, or misconfigured |
| 20 | Keychain import failed |
| 21 | OpenDirectory write failed |
| 22 | `ldap.conf` write failed |
| 23 | Search path configuration failed |
| 24 | Post-install verification failed |

---

## Mobile accounts and FileVault

Network accounts can't log in offline and can't unlock FileVault unless they have a mobile
account. `fvhelper.py` creates them. It is **deliberately separate and interactive**, because it
needs an admin password that must not live on the flash drive.

Run it after `install.sh` and after the Mac has rebooted:

```sh
sudo ./python/Versions/Current/bin/python3 fvhelper.py alice bob
```

With no usernames it prompts for a list. It checks each name resolves in LDAP first, and warns if
the admin account lacks a SecureToken (without one, the accounts get created but won't be able to
unlock FileVault).

---

## Repository layout

```
deploy.sh                        Admin-side payload builder
src/install.sh                   Launcher: runs install.py under the bundled interpreter
src/install.py                   The provisioning logic
src/ldap.google.com.plist.in     LDAP config template (search base + UUID substituted at build)
src/fvhelper.py                  Mobile account / FileVault helper
vendor/relocatable-python/       Vendored build tool for the bundled Python framework
                                  (see VENDORED.md there for provenance/update instructions)
```

The payload that `deploy.sh` produces also contains `config.json` (non-secret settings), `.env`
(the p12 password), `assets/` (the rendered plist and the `.p12`), a bundled Python framework with
pyobjc, and a `manifest.sha256`.

Nothing from the payload is installed onto the target Mac other than the keychain identity and
the directory configuration — the Python interpreter runs from the drive, and the `.p12` is never
copied to local disk.

---

## Security

**Certificates and keys must never be committed to this repository.** `.gitignore` blocks
`*.key`, `*.p12`, `*.pfx`, and `*.pem`. An earlier version of this project did commit them; that
history has been purged, but treat any credential that has ever touched git as compromised and
reissue it.

**The flash drive is the security boundary, not the `.env` password.** The p12 password sits in
`.env` right next to the `.p12` it protects, so anyone holding the drive has both. Encrypting the
key at rest and rotating the password per build is still worth doing, but the controls that
actually matter are:

- **physical custody of the drive** — it holds credentials that can enumerate your directory;
- the **non-extractable** keychain import, so a provisioned Mac can't surrender the key;
- **deleting the certificate** in Google Admin when a drive is lost or retired;
- **short certificate lifetimes**, so a missed drive expires on its own.

To revoke access, **delete the certificate** in Google Admin (Apps → LDAP → your client →
Authentication). Deletion is immediate; turning Service Status off can take up to 24 hours.

### What the credential grants

Enumerating users, reading profile details, and reading group membership for whatever scope you
granted the LDAP client. Scope it to the OUs you actually need.

---

## Notes for future maintainers

The OpenDirectory configuration is applied through an **undocumented** OpenDirectory custom call
(`customCall_sendData_error_(99991, …)`, with a 32-byte zeroed authorization reference prefixed to
the plist). This is what Google's own macOS instructions use, and it's the only technique known to
work in practice, but Apple explicitly does not support it:

- Writing the config plist directly to `/Library/Preferences/OpenDirectory/Configurations/` does
  **not** work — SIP has blocked it since Catalina, and no amount of signing helps.
- Configuration profiles can't do this either: `profiles install` was removed in macOS 11, and the
  `com.apple.DirectoryService.managed` payload is Active-Directory-only — it has no keys for
  LDAPv3 attribute mappings, RFC 2307 schema, or a TLS client identity.
- `dscl … CSPSearchPath` is used for the search path deliberately, since it goes *through*
  `opendirectoryd` rather than writing the SIP-protected `Search.plist`.

`install.py` checks every return value from the custom call and then independently confirms the
config file appeared, so if a future macOS release breaks it you get a clear failure rather than a
half-configured Mac. The documented replacement, should that day come, is
`ODSession.addConfiguration:authorization:error:` (undeprecated since 10.9) — but no one has
published a Google-shaped configuration built through it, so expect a spike.

**One piece of history worth not relitigating:** older versions shipped separate
`intel-client.p12` and `arm-client.p12` files, with the code selecting between them by CPU
architecture. Those two files were verified to be *cryptographically identical* — same
certificate serial, same fingerprint, same public key, same encryption parameters — differing only
in random export salt. PKCS#12 is an architecture-neutral format; there was never any reason for
two files. Whatever import failure originally prompted the split had another cause. One `.p12`
works everywhere.

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

**Before your first deployment**, complete the one-time Google Admin console setup in
[GOOGLE_WORKSPACE_SETUP.md](GOOGLE_WORKSPACE_SETUP.md) — you need the certificate it produces to
run `deploy.sh` at all.

---

## Requirements

- **Targets:** Apple Silicon only, macOS 15 or newer. Intel is not supported.
- **Admin machine:** macOS, with network access on the first build (it downloads a Python
  framework from python.org). No extra tooling is required beyond what macOS already ships.
- **Google Workspace:** Enterprise, Education, or Cloud Identity Premium — Secure LDAP is not
  available on other tiers.

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

`deploy.sh` checks the certificate and key before it builds anything, and stops if:

- the certificate has **expired** (or warns if it expires within 30 days),
- the key and certificate **aren't a pair**,
- Google **rejects the credential** — it tests the cert against Google's servers for real,
- the generated `.p12` **can't be imported** by macOS's own keychain tools.

That last check runs on your admin Mac using a throwaway keychain, so a bad certificate shows up
at your desk instead of halfway through a cart of Macs.

**A run only succeeded if it ends with a `BUILD SUCCEEDED` banner.** If it stops early with a
`BUILD FAILED` message, the script has already deleted the incomplete payload for you — fix the
reported problem and run it again. Don't copy a payload from a run you didn't see finish.

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

It verifies the payload, configures the machine, prints `PASS` or `FAIL`, and reboots. The
reboot is required to activate the login configuration — there's no way to skip it.

Flags: `--no-reboot` (leaves the login configuration inactive until you reboot manually),
`--dry-run` (checks and reports without changing anything).

Re-running on an already-provisioned Mac is safe — every step can be run again without harm.

### What it does

1. Verifies payload integrity (checks every file against the manifest).
2. Imports the client identity into the **System** keychain, non-extractable, granting access to
   `opendirectoryd`, `dscl`, and Directory Utility.
3. Writes the LDAP configuration into OpenDirectory, then confirms it landed.
4. Sets the TLS client identity in `/etc/openldap/ldap.conf`.
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
account. `fvhelper.py` creates them. It's **deliberately separate and interactive**, because it
needs an admin password that must not live on the flash drive.

Run it after `install.sh` and after the Mac has rebooted:

```sh
sudo ./python/Versions/Current/bin/python3 fvhelper.py alice bob
```

With no usernames it prompts for a list. It checks each name resolves in LDAP first, and warns if
the admin account lacks a SecureToken (without one, the accounts get created but won't be able to
unlock FileVault).

---

## What's in the payload

`deploy.sh` produces one folder — copy the whole thing to the flash drive:

- `install.sh` / `install.py` / `fvhelper.py` — the scripts above
- `config.json` — non-secret settings (search base, banner text, cert details)
- `.env` — the p12 password (mode 600)
- `assets/` — the rendered LDAP config and the `.p12` client certificate
- `python/` — a bundled Python interpreter, so target Macs need nothing pre-installed
- `manifest.sha256` — the integrity manifest `install.sh` checks on every run

Nothing from this folder ends up on the target Mac other than the keychain identity and the
directory configuration — the interpreter runs from the drive, and the `.p12` never touches local
disk.

---

## Security

- **Physical custody of the drive matters.** It holds a credential that can enumerate your
  directory, plus the password for it (`.env`, sitting right next to the `.p12`). Protect the
  drive like you would a set of admin credentials.
- **If a drive is lost or retired, revoke it immediately** by deleting the certificate in Google
  Admin (Apps → LDAP → your client → Authentication). Deletion is immediate; turning Service
  Status off instead can take up to 24 hours.
- Keep certificate lifetimes short, so a drive that's lost and not reported still expires on its
  own.
- The keychain import on each Mac is **non-extractable** — a provisioned Mac can't be used to
  recover the key.
- **Certificates and keys must never be committed to this repository.** `.gitignore` blocks
  `*.key`, `*.p12`, `*.pfx`, and `*.pem`.

### What the credential grants

Enumerating users, reading profile details, and reading group membership for whatever scope you
granted the LDAP client. Scope it to the OUs you actually need.

---

For source layout and engineering rationale (why things are built the way they are), see
[TECHNICAL_NOTES.md](TECHNICAL_NOTES.md).

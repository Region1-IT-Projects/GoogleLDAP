# Technical notes

Engineering background for people maintaining this repository's code. Nothing here is needed to
build a payload or provision a Mac — see [README.md](README.md) for that.

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

The payload `deploy.sh` produces also contains `config.json` (non-secret settings), `.env` (the
p12 password), `assets/` (the rendered plist and the `.p12`), a bundled Python framework with
pyobjc, and a `manifest.sha256`. See the README's "What's in the payload" section for the
operator-facing version of this.

---

## How install.py configures OpenDirectory

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

---

## Why install.sh forces a reboot

`opendirectoryd` can no longer be reliably restarted in place — Apple blocked
`launchctl kickstart -k` for system processes in macOS 14.4 — so a reboot is the only way to
activate a new directory binding. `--no-reboot` exists for scripted batches that reboot on their
own schedule, not as a way to avoid rebooting altogether.

---

## PKCS#12 export parameters

`deploy.sh` exports the client identity with `PBE-SHA1-3DES` / SHA-1 MAC rather than OpenSSL 3's
modern defaults (AES-256-CBC/PBKDF2/SHA-256). macOS 14 and earlier cannot read the modern format
at all, and 3DES avoids needing OpenSSL 3's legacy provider (unlike the RC2-40 that Keychain
Access itself exports). The self-test step in `deploy.sh` (importing into a throwaway keychain)
exists specifically because this combination is finicky enough that catching it here beats
catching it on machine #40 of a deployment.

---

## History: the intel-client.p12 / arm-client.p12 split

Older versions of this tool shipped separate `intel-client.p12` and `arm-client.p12` files, with
the code selecting between them by CPU architecture. Those two files were verified to be
*cryptographically identical* — same certificate serial, same fingerprint, same public key, same
encryption parameters — differing only in random export salt. PKCS#12 is an architecture-neutral
format; there was never any reason for two files. Whatever import failure originally prompted the
split had another cause. One `.p12` works everywhere.

---

## Repository history note

An earlier version of this project committed certificates and keys directly. That history has
been purged from git, but treat any credential that ever touched this repository — even in purged
history — as compromised, and reissue it.

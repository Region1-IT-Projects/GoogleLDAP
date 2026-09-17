# Google Workspace setup

One-time setup in the Google Admin console, needed before you build your first payload with
`deploy.sh`. Do this once per domain, not once per Mac. See [README.md](README.md) for how to
use the tool itself once this is done.

---

## Enable Secure LDAP and get a certificate

Under **Apps → LDAP**:

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

---

## Two limitations worth knowing before you deploy

- A user's Workspace username **must differ from their local macOS profile's user ID**, or
  sign-in is blocked. This is a Google-documented constraint, not a bug in this tool.
- **Internet access is required for a user's first login.** Offline logins afterward require a
  mobile account — see [Mobile accounts and FileVault](README.md#mobile-accounts-and-filevault)
  in the README.

# MacOS LDAP Authentication

This is a set of scripts to bind MacOS computers to the Region 1 Google Workspace domain via LDAP.


## Usage

Download all of the files in the root of this repository (the `certs` directory can be ignored) to the target Mac, open a terminal in the directory where you put the files, and run `zsh runme.sh`. Enter your local admin password when prompted. That's it, everything else is automatic.

### Mobile Accounts (Optional)

If you want to configure mobile accounts for offline logins and/or FileVault access, follow the above steps but press `crtl+c` during the 6 second countdown before the system restarts. This will lauhch the FileVault heler script. Enter the usernames for each user you would like to have a mobile account (space separated) and press enter. Enter your local admin password when prompted. If all goes well, the script will vomit some text onscreen then return to the reboot countdown. Allow the system to reboot as normal.

## Structure

### `runme.sh`
This shell script exists to bootstrap the rest of the process. Modern versions of MacOS do not ship with the proper versions of python or the python bindings for MacOS libraries. This script grabs a copy of python 3.11, installs it, then calls `main23.pyobjc`

### `main23.pyobjc`
This is the main script for this project. Its file extension reflects the fact that it calls bindings to objective-c libraries to interact with the MacOS system. This script detects the architecture of the host, selects the proper `p12` file, verifies the existance of said `p12` and the `plist` file, then applies them to the system. It also sets up the login screen and other small stuff.

### `XXX-client.p12`
This is a certificate file that allows MacOS to query Google LDAP and verify user accounts. This certificate is architecture specific (Don't ask me why, I have no clue) and the main script selects the correct one automatically. This script is also password protected, but that password is hard-coded in the main script.

### `ldap.google.com.plist`
This is a Apple Property List that contains the main configuration to tell MacOS to authenticate against Google LDAP. This file is architecture agnostic.

### fvHelper.py
This is a small script to help automate mobile account creation, basically just a wrapper around the MacOS `createmobileaccount` utility that allows bulk adding. 




*Security Notice:* The files contained within this git repository include a certificate and passphrase pair that allow query access to Region 1 google accounts. This includes: Enumerating users, viewing public details about user profiles, viewing group membership. 

Is it stupid to have these files included with a hard-coded passphrase? Yes. That is why this repo is private and all files within it should be treated as sensative. These files are here for the sake of ease of access and unattended deployment, and I deemed the risk to be acceptable. This certificate can always be revoked in google admin should the need arise. 


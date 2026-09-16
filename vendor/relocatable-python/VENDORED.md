Vendored from https://github.com/gregneagle/relocatable-python
at commit `8ee72fe3a5dbef733365370ebf44f25022b895ef` (2024-10-08), Apache License 2.0.

Vendored (rather than `git clone`d at build time by `deploy.sh`) so the admin machine doesn't
need `git` installed, and so the build is pinned to a known-good version instead of tracking
`main`. To update, re-fetch the files under `locallibs/` and `make_relocatable_python_framework.py`
from a newer commit and update the SHA above.

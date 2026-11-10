---
description: Link a Yoto account (or check which are linked)
argument-hint: "[name]"
---

Link the Yoto account named "$ARGUMENTS" (if blank, just show the linked accounts).

1. Call `yoto_whoami`. If `client_configured` is false, stop and tell the user to run this once in
   their terminal, then try again:
   `! uvx --from git+https://github.com/earchibald/yoto-mcp yoto-mcp setup`
   (it asks for the Yoto app's client id and secret and keeps them in the macOS keychain).
2. If a name was given, call `yoto_account_link(name)`. Tell the user to finish logging in in the
   browser, choosing the right Yoto account. Then call `yoto_whoami` again to confirm.
3. List the linked accounts by name and say which is the default.

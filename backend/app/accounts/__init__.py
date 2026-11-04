"""Accounts: sign-in, registration, two-factor, email verification and password reset (M6).

Moved here from gecko-notes, which was the suite's identity provider until M6. Ported, not
redesigned: the flows, limits and messages are Notes', and so is the token. The suite signs
the same HS256 token with the same shared secret and the same claims (plus `email` and
`is_admin`, which the other apps ignore) and sets the same cookie, so a session from either
issuer is a session everywhere. That is what makes the cutover a configuration change that
can be undone, and why no other app's verification changes.
"""

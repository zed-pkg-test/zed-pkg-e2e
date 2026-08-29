# Shared Auth exact-head certification

Last certified commit: `a471b20f8f81094f57393dc797b3cad0868c2f40`

Next requested exact head (shared-auth `main`, not yet re-certified):
`5cb47d3169df6829c468ea724e8acd61fecbcd6d`. Independent test repo head
requested: `426fa32554b99f3e555d578dc0b99b2b8a9c4ff0`.

The Zpkg browser-handoff source passed the independent Shared Auth test contract,
declarative PostgreSQL schema application, realm validation, Redis reachability,
first-use/replay/PKCE/redirect/client/expiry database boundaries, concurrent
single-consumer redemption, locked formatting, compile, Clippy with warnings denied,
all-target tests, and a release build.

Independent contract commit: `f41cdf668bbedab9ed75d01266a2086fa87b9ed1`.

This is disposable test-environment evidence, not production Supabase, RDS,
Kubernetes, browser, or traffic certification.

# Explicit local PDF trust store

DocuVerify never falls back to the operating-system TLS trust store for PDF
signature validation. Place separately reviewed issuer root/intermediate
certificates in this directory for local use. Certificate files are ignored by
Git; only this policy README is committed.

An empty directory is valid. In that state, a mathematically valid signature is
reported as **cryptographically valid but signer trust unknown**.

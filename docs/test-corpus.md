# Test data policy

> **Reference status:** Current repository test-data policy for the v0.2.26 documentation set.

The public repository uses only synthetic fixtures created by the tests themselves.
No maintainer disk-image collection, filenames copied from a private collection,
local approval records, source hashes, private paths, or machine-specific
configuration are included.

For local qualification against a personal collection, configure the application
outside the repository and keep that data untracked. Public tests must remain
self-contained and synthetic.

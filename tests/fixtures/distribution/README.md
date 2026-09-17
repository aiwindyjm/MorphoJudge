# Portable TypeScript fixture

`ts-web.bundle` contains only the two synthetic commits listed in
`../manifests/ts-web-manifest.json`. Its SHA-256 is recorded there.
It has no prerequisite Git objects and requires no network to restore.

From the project root:

```sh
docker compose --profile fixtures run --rm --build fixture-setup
docker compose up --build -d web daemon
docker compose exec -T daemon pytest -q
```

The setup service restores the repository to the project's `fixture-data`
Docker volume. The daemon mounts that volume read-only. Re-running setup
validates the commits and working tree without overwriting them. A checksum
mismatch, changed worktree or incomplete restoration causes an explicit error;
inspect the dedicated volume rather than deleting unrelated Docker data.

The fixture intentionally contains **fake** credential values, a `PRIVATE/`
test directory, malformed TypeScript and shell/process examples. These are
static test inputs, not application code or real private records. Never run the
fixture's scripts, install its packages, or publish analysis of real user code.
The project-root `PRIVATE/` directory is never included.

The historical local repository at `tests/fixtures/ts-web/` is now ignored and
is not needed after cloning. Its original base/target identities are preserved
inside this bundle.

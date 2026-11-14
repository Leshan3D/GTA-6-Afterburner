# OpenCode handoff: choosing the correct procedure

**For an existing functioning AFTERBURNER v0.1.0 installation** (the user's situation), follow **[`OPENCODE_UPGRADE.md`](OPENCODE_UPGRADE.md)**. Back up the local config, preserve SQLite history and only modify the standalone AFTERBURNER project. Do not overwrite JAFAR.

**For a new installation on another Linux PC**, follow [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md), [`docs/TELEGRAM.md`](docs/TELEGRAM.md) and [`docs/PUBLIC_SHARING.md`](docs/PUBLIC_SHARING.md). The v0.2 installer supports clone locations other than the user's original NTFS mount.

**Required validations:** 16+ passing offline unit tests, private dashboard on `127.0.0.1:5056`, public-only preview on `127.0.0.1:5057`, 404 for all private API routes on the public preview, zero exchange order endpoints and no leak of bot tokens or chat IDs. External URL and GitHub push only after explicit user approval.

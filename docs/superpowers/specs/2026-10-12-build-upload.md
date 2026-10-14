# Загрузка билда

Add a Russian-language sidebar tab that reuses current ISSUER_ID, KEY_ID, APP_ID,
and PRIVATE_KEY_PATH fields. A GitHub repository URL (or local repository folder
for discovering origin) and one button configure and run Flutter iOS builds and
upload to App Store Connect. Build source is the remote default branch; local
uncommitted code is never published implicitly. An optional checkbox enables
future builds on pushes to that branch.

Use GitHub Desktop authentication on macOS, with an optional session-only token
fallback. Verify Apple app identity and the remote Flutter bundle before writing
secrets. Preserve existing distribution certificate secret. Encrypt secrets with
GitHub's public key, generate RSA signing material only when needed, and never
save Apple/GitHub secrets in workflow files or tab preferences. Own a separate,
marked workflow file; refuse to replace unrelated contents. Pin action revisions.

Run requests on a background QThread. Show configuration, queue/build/upload
steps, results and links. A successful upload means Apple processing may remain;
it never means App Review submission. Persist per-app repository preferences and
last run ID, allow resuming observation, and stop observation safely on closing.

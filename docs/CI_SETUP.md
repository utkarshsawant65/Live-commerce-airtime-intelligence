# CI/CD Setup — GitHub Actions + Snowflake key-pair auth

The CI workflow ([.github/workflows/ci.yml](../.github/workflows/ci.yml)) runs on every PR to
`main`:
1. **lint-and-parse** (always) — installs deps, `dbt parse` (DAG valid), sqlfluff lint.
2. **build-and-test** (when Snowflake secrets exist) — generates a small batch, loads RAW,
   runs `dbt build` (every model + all tests) and `dbt source freshness` against Snowflake.

A red run blocks the merge once you enable branch protection (last section). CI authenticates
to Snowflake with a **key-pair** (no password in the pipeline) — the production-standard
approach. Follow these one-time steps.

---

## 1. Generate an RSA key-pair (run locally)

Snowflake key-pair auth needs an **unencrypted PKCS#8** private key. In Git Bash:

```bash
# Private key (PKCS#8, unencrypted) + public key.
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -nocrypt -out rsa_key.p8
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

You now have `rsa_key.p8` (private — secret) and `rsa_key.pub` (public). **Do not commit either**
(they match the `*.key`/`*.p8` gitignore rules; double-check before any `git add`).

## 2. Register the PUBLIC key on your Snowflake user

Copy the public key body **without** the header/footer lines and newlines:

```bash
# Prints the key as a single line — copy the output.
grep -v "PUBLIC KEY" rsa_key.pub | tr -d '\n'
```

Then in a Snowsight worksheet (as ACCOUNTADMIN), paste that string into:

```sql
alter user <YOUR_SNOWFLAKE_USER> set rsa_public_key='PASTE_THE_SINGLE_LINE_KEY_HERE';
```

Verify it took:
```sql
desc user <YOUR_SNOWFLAKE_USER>;   -- look for RSA_PUBLIC_KEY_FP (a fingerprint) in the output
```

## 3. Add GitHub repository Secrets

In your repo on GitHub → **Settings → Secrets and variables → Actions → New repository secret**.
Add these three:

| Secret name | Value |
|---|---|
| `SNOWFLAKE_ACCOUNT` | your account identifier, e.g. `ORGNAME-ACCOUNTNAME` |
| `SNOWFLAKE_USER` | your Snowflake user name |
| `SNOWFLAKE_PRIVATE_KEY` | the **base64-encoded** `rsa_key.p8` (single line — see below) |

The private key must be stored **base64-encoded**, not raw. A raw multiline PEM gets its
line breaks mangled in a GitHub Secret, producing "Unable to load PEM file" in CI. base64 is
a single line, so it round-trips intact; the workflow decodes it back to a PEM at runtime.

Generate the single-line value and copy it to your clipboard (Git Bash, from repo root):
```bash
base64 -w0 rsa_key.p8 | clip      # -w0 = no line wrapping (one line)
```
Paste that single line into the `SNOWFLAKE_PRIVATE_KEY` secret value.

> The workflow writes `SNOWFLAKE_PRIVATE_KEY` to a file at runtime and points dbt's `ci`
> target at it via `private_key_path` — the key never lands in the repo.

## 4. Trigger and watch the workflow

Push a branch and open a PR to `main` (or use the **Actions** tab → CI → **Run workflow**).
- **lint-and-parse** should pass immediately.
- **build-and-test** connects with the key-pair, loads a 2-day batch, and runs `dbt build`.
- Green check = CI is live.

## 5. Make CI block bad merges (the point of it)

GitHub → **Settings → Branches → Add branch protection rule** for `main`:
- ✅ Require a pull request before merging
- ✅ Require status checks to pass → select **lint-and-parse** and **build-and-test**
- (optional) ✅ Require branches to be up to date before merging

Now a PR with a failing model or test **cannot be merged** — the "CI blocks a red PR"
guarantee. You can prove it by opening a deliberately-broken PR once.

---

## Local dev auth (unchanged)

Local development still uses **password** auth via `.env` + the `dev` target — simpler for
interactive work. Key-pair is specifically for unattended CI. (You can also switch local dev to
key-pair by setting `SNOWFLAKE_PRIVATE_KEY_PATH` and using `--target ci`.)

## Security note

Rotate any credential that has been shared or shown. To remove the CI key from
Snowflake: `alter user <YOUR_SNOWFLAKE_USER> unset rsa_public_key;`

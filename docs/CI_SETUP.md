# CI Setup

The workflow in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs on pull
requests and pushes to `main`, and can be started manually. Runs are serialised (one at a
time) because they share the RAW schema.

| Job | When | What it does |
|---|---|---|
| `parse` | always | `dbt deps` + `dbt parse` offline with placeholder credentials; generates a 2-day batch and runs the z-test as a smoke test |
| `check-secrets` | always | reports whether `SNOWFLAKE_ACCOUNT` is set (secrets cannot be read in a job-level `if`) |
| `build-and-test` | only if the secret exists | key-pair login, `dbt debug`, loads a 2-day batch into RAW with `--truncate`, runs `drop_ci_schemas` then `dbt build --target ci --full-refresh`, `sqlfluff lint` (blocking, dbt templater), `dbt source freshness` (report only) |

`build-and-test` sets `DBT_TARGET=ci` and `DBT_SCHEMA_SUFFIX=_CI`, so models land in
`STAGING_CI`, `INTERMEDIATE_CI`, `MARTS_CI` and `SNAPSHOTS_CI`. `drop_ci_schemas` only drops
schemas with that suffix and does nothing unless the target is `ci`.

**RAW is shared.** CI truncates and reloads `LIVE_COMMERCE.RAW`; it is not suffixed. If you
also develop locally against the same database, a CI run replaces your RAW data. Use a
separate Snowflake database or account for CI in that case.

Freshness is report-only because the batch covers a fixed past window.

## 1. Snowflake user and roles

The CI job loads RAW (as `ROLE_LOADER` on `WH_LOAD`) and runs dbt (as `ROLE_TRANSFORMER` on
`WH_TRANSFORM`), so its user needs both roles. `snowflake/00_setup.sql` creates them; uncomment
the `ci_user` block in section 4 to grant them. `ROLE_TRANSFORMER` has `CREATE SCHEMA` on the
database, which is what lets dbt create the `_CI` schemas.

## 2. Key pair

The `ci` profile uses `private_key_path` with no passphrase, so generate an unencrypted
PKCS#8 key:

```bash
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -nocrypt -out rsa_key.p8
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
```

Both files are gitignored (`*.p8`, `*.pub`, `rsa_key*`). Register the public key on the CI
user:

```bash
grep -v "PUBLIC KEY" rsa_key.pub | tr -d '\n'     # prints the key on one line
```

```sql
alter user <CI_USER> set rsa_public_key = '<KEY_FROM_ABOVE>';
desc user <CI_USER>;    -- RSA_PUBLIC_KEY_FP should now be set
```

## 3. GitHub secrets

Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `SNOWFLAKE_ACCOUNT` | account identifier, e.g. `ORGNAME-ACCOUNTNAME` |
| `SNOWFLAKE_USER` | the CI user |
| `SNOWFLAKE_PRIVATE_KEY` | `rsa_key.p8`, base64-encoded on one line |

```bash
base64 -w0 rsa_key.p8        # copy the output into the secret
```

A raw multi-line PEM gets mangled when stored as a secret, so the workflow stores it base64
and decodes it to a file at runtime, then checks it with `openssl pkey`.

## 4. Branch protection

Settings → Branches → add a rule for `main`: require a pull request and require the `parse`
and `build-and-test` checks to pass. When the secrets are missing (for example on forks),
`build-and-test` is skipped rather than failed.

## Local development

`dbt/profiles.example.yml` picks the target from `DBT_TARGET` (default `dev`). `dev` uses
password auth from `.env`. To use a key pair locally, set `SNOWFLAKE_PRIVATE_KEY_PATH`
(and `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` if the key is encrypted, which only the loader and
z-test support) and run dbt with `--target ci`. Without `DBT_SCHEMA_SUFFIX` that builds into
the unsuffixed schemas.

To revoke the CI key: `alter user <CI_USER> unset rsa_public_key;`

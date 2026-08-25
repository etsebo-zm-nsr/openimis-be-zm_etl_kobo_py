# Zambia Social Registry — KoboToolbox ETL connector

`openimis-be-zm_etl_kobo` pulls new field registrations from KoboToolbox into openIMIS.

A **connector**: it owns only Kobo-specific concerns — `next`-URL pagination, the
Mongo-style `_submission_time` filter, and Token auth. Sink, record linkage, sync state,
scheduling and HTTP plumbing come from the shared `api_etl` core.

## Install

```json
{ "name": "zm_etl_kobo", "pip": "-e /path/to/openimis-be-zm_etl_kobo_py" }
```

Must be listed **after** `api_etl` in `openimis.json`.

## Configure

Set the asset URL and token on the `zm_etl_kobo` `ModuleConfiguration` row:

```json
{ "source": { "url": "https://kf.kobotoolbox.org/api/v2/assets/<asset_uid>/data/" },
  "auth":   { "token": "env:KOBO_API_TOKEN" },
  "adapter": { "field_map": { "first_name": "household/hoh_first_name" } } }
```

Kobo uses `Authorization: Token <t>`, not Bearer — hence `auth.scheme: "Token"`.
Never store the token itself; `env:` reads it from the process environment.

## Incremental sync

Ascending `sort` on `_submission_time` plus a `$gte` watermark, with a 15-minute
overlap because Kobo's submission-time ordering is not strictly monotonic against
ingest. Re-pulling the overlap is free — `external_id` upserts are idempotent.

## Rights

`953102` — trigger the Kobo sync. Granted to *IMIS Administrator* by migration `0001`.

from django.apps import AppConfig

MODULE_NAME = "zm_etl_kobo"

KOBO_TRIGGER_RIGHT = "953102"

DEFAULT_CONFIG = {
    "enabled": True,
    "auth": {
        # Kobo uses "Authorization: Token <t>", NOT Bearer.
        "type": "token",
        "scheme": "Token",
        "token": "env:KOBO_API_TOKEN",
    },
    "source": {
        "http_method": "GET",
        # https://kf.kobotoolbox.org/api/v2/assets/<asset_uid>/data/
        "url": "",
        "batch_size": 500,
        "timeout_seconds": 120,
        "retry_total": 3,
        "verify_ssl": True,
        "response": {
            "rows_key": "results",
            "next_key": "next",
        },
    },
    "adapter": {
        # Kobo flattens repeat groups into "group/question" keys.
        "field_map": {
            "first_name": "first_name",
            "last_name": "last_name",
            "dob": "dob",
            "location_name": "location_name",
            "location_code": "location_code",
        },
        "external_id_field": "_uuid",
        "external_id_prefix": "kobo:",
        "group_code_field": "household_id",
        "group_code_prefix": "zm-hh-kobo:",
        "role_field": "relationship",
        "role_map": {
            "head": "HEAD", "spouse": "SPOUSE", "son": "SON", "daughter": "DAUGHTER",
            "other_relative": "OTHER_RELATIVE", "not_related": "NOT_RELATED",
        },
        "recipient_field": "is_recipient",
        "national_id_field": "nrc",
        "date_formats": ["%Y-%m-%d", "%d/%m/%Y"],
    },
    "sink": {
        "lookup_field": "json_ext__external_id",
        "update_existing": True,
    },
    "incremental": {
        "enabled": True,
        "mode": "kobo_mongo",
        "cursor_field": "_submission_time",
        # Kobo's _submission_time ordering is not strictly monotonic against ingest, so
        # re-pull a small overlap. Free, because external_id upserts are idempotent.
        "overlap_minutes": 15,
    },
    "provenance": {
        "data_source_label": "KOBO",
        "source_type": "api etl:kobo",
        "batch_prefix": "kobo_",
    },
    "schedule": {
        "enabled": False,
        # New registrations should appear quickly; polling is self-healing across
        # restarts in a way a webhook is not.
        "cron": {"minute": "*/15"},
    },
    "gql_mutation_trigger_perms": [KOBO_TRIGGER_RIGHT],
}


class ZmEtlKoboConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = MODULE_NAME

    def ready(self):
        from api_etl.config import config_from_module
        from api_etl.registry import register_etl_source
        from zm_etl_kobo.service import KoboETLService

        register_etl_source(
            name="kobo",
            service_cls=KoboETLService,
            config_provider=lambda: config_from_module(MODULE_NAME, DEFAULT_CONFIG),
            label="KoboToolbox (field registrations)",
            trigger_perms=[KOBO_TRIGGER_RIGHT],
        )

"""KoboToolbox connector configuration.

Mapped against the live Kobo API v2 data endpoint
(`/api/v2/assets/<uid>/data/?format=json`), checked against a real submission: every
path below resolves on real data.

SHAPE. One Kobo submission is one HOUSEHOLD, and its roster (`household_roster_sec`) is a
list with one dict per member. `KoboAdapter.transform` explodes each submission into one
row per roster member, so every member becomes one registry record and the household's
own answers (location, dwelling, energy, enumerator) are copied onto each of them, as the
ZISPIS connector does. Fields that belong to the head alone (`head_only_fields`) are kept
on the head's record only. The household-identification section repeats the head's name,
date of birth and sex; that copy is NOT imported, because the head is also in the roster
and would otherwise be registered twice.

VALUES are stored as sent. Kobo sends choice codes (`iron_sheets`, `own_child`), which are
NOT yet harmonised with the ZISPIS vocabularies; reconciling the two is a data-owner
decision, not something to guess here.

SCOPE. Only the identity, household-structure, location, dwelling and energy answers are
mapped. The wider questionnaire (assets, agriculture, health, protection) is not mapped
yet.

Everything here is runtime configuration: it ships as DEFAULT_CONFIG and is overridden
per deployment through the Django admin, never through a migration.
"""
from django.apps import AppConfig

MODULE_NAME = "zm_etl_kobo"

KOBO_TRIGGER_RIGHT = "953102"

# Path prefixes inside a submission. Roster keys keep the full path inside each member
# dict, which is why the prefix is needed on every member field.
ROSTER = "consented_grp/household_roster_sec"
_R = ROSTER + "/"
_H = "consented_grp/household_identification_sec/"
_C = "consented_grp/household_characteristics_sec/"

# target registry column -> Kobo path ("a/b/c" resolves a literal key first, then nesting)
KOBO_FIELD_MAP = {
    # -- base fields (required by individual_base_fields) --
    "first_name": _R + "first_name_hmember",
    "last_name": _R + "last_name_hmember",
    "dob": _R + "dob_hmember",
    # Kobo sends names only. `location_name` is mapped for completeness but BOTH it and
    # `location_code` are overwritten by the core's location resolver, which matches
    # district / constituency / ward against the loaded gazetteer
    # (adapter.location_match_fields below).
    "location_name": "ward_name",

    # -- member --
    "sex": _R + "gender_hmember",
    # Stored exactly as sent; nothing is computed from it (see KoboAdapter.transform_row).
    "age": _R + "calc_hmember_age",
    "marital_status": _R + "marital_status_hmember",
    "birth_certificate_number": _R + "birth_certificate_hmember_num",
    "educated_level": _R + "education_completed",
    "school_attendance": _R + "school_attendance_status",
    "school_name": _R + "enrolled_sec/school_name",
    "disability": _R + "member_disability",

    # -- household: location (copied onto every member) --
    "province": "province_name",
    "district": "district_name",
    "constituency": "constituency_name",
    "ward": "ward_name",

    # -- household: registration --
    "registered_at": "_submission_time",
    "enumerator_name": "enumerator_name",
    "enumerator_phone": "enumerator_phone_num",
    "informed_consent": "consent_grp/consent",
    # The household's phone belongs to the head (adapter.options.head_only_fields).
    "phone": _H + "phone_num_hh",

    # -- household: dwelling and energy --
    "roof_material": _C + "roof_type",
    "wall_material": _C + "walls_type",
    "floor_material": _C + "floor_type",
    "cooking_energy": _C + "main_cooking_energy_type",
    "lighting_energy": _C + "main_light_energy_type",
}

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
        # A page is 100 HOUSEHOLDS, about 440 people once exploded - the size of one
        # import batch.
        "batch_size": 100,
        "timeout_seconds": 120,
        "retry_total": 3,
        "verify_ssl": True,
        "response": {
            "rows_key": "results",
            "next_key": "next",
        },
    },
    "adapter": {
        "field_map": KOBO_FIELD_MAP,
        # Set by KoboAdapter.transform when it explodes a submission; see adapter.py.
        "external_id_field": "_etl_member_id",
        "external_id_prefix": "kobo:",
        "group_code_field": "_etl_household_id",
        "group_code_prefix": "zm-hh-kobo:",
        # Relationships that need the member's gender are resolved in
        # KoboAdapter.map_role; this covers the ones that do not.
        "role_field": ROSTER + "/relationship_hh_hmember",
        "role_map": {
            "head": "HEAD",
            "spouse": "SPOUSE",
            "step_child": "OTHER_RELATIVE",
            "cousin": "OTHER_RELATIVE",
            "niece_nephew": "OTHER_RELATIVE",
            "brother_sister_in_law": "OTHER_RELATIVE",
            "parent_in_law": "OTHER_RELATIVE",
            "other_relative": "OTHER_RELATIVE",
            "non_relative": "NOT_RELATED",
        },
        # The head is the household's recipient; the core maps "head" to primary.
        "recipient_field": ROSTER + "/relationship_hh_hmember",
        "national_id_field": _R + "nrc_num_hmember",
        "date_formats": ["%Y-%m-%d"],
        # An impossible date resolves to None rather than being asserted as fact.
        "min_year": 1900,
        # KoboAdapter attaches these in code. `dob_as_sent` only on rows whose date was
        # sent but unusable. Declared so `etl_check_schema` counts them.
        "extra_columns": ["dob_as_sent", "year_of_birth", "source_member_ref"],
        # Same definition of "same person" as ZISPIS, so the two sources can be matched.
        "identity_key_fields": ["first_name", "last_name", "year_of_birth", "sex",
                                "district"],
        "include_when": {},
        # Resolve the ward against the loaded gazetteer; Kobo sends names only. The
        # province is left out for the reason given in the ZISPIS connector.
        "location_match_fields": ["district", "constituency", "ward"],
        # Spellings Kobo uses that the gazetteer does not; the data owner's call.
        "location_aliases": {},
        # Kobo-only settings; the core never reads these (adapter.options is free-form).
        "options": {
            # Where the household roster (a list of member dicts) sits in a submission.
            "roster_field": ROSTER,
            # Stable across edits (`_uuid` changes on every edit, `meta/rootUuid` does
            # not). A leading "uuid:" is stripped.
            "household_id_field": "meta/rootUuid",
            # How a member's id is built. Alternatives are tried in order; the first
            # whose parts ALL resolve wins, and its parts are joined with ":". The
            # synthetic keys `_etl_household_id` and `_etl_member_position` (1-based
            # place in the roster) are available as parts.
            #
            # The form does not export a per-member id, so the position is all there is.
            # That is fragile: deleting a member in an edit shifts everyone after them
            # and silently re-points their ids. Once the form gets a per-member UUID,
            # switch here - no code change - to use it where present and the position
            # for older submissions:
            #
            #     [[ROSTER + "/member_uuid"], ["_etl_household_id", "_etl_member_position"]]
            #
            # People already imported under the positional id then need a one-off remap
            # to the UUID id. That is reconcilable because every record carries
            # `source_member_ref` (household:position), which stays positional whatever
            # scheme is configured.
            "member_id_fields": [["_etl_household_id", "_etl_member_position"]],
            # field_map targets kept on the HEAD's record only: the household phone
            # belongs to the head, not to every child.
            "head_only_fields": ["phone"],
        },
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
        #
        # An EDITED submission keeps its original `_submission_time`, so incremental sync
        # never picks edits up. A periodic full re-pull (reset the cursor) does, and is
        # idempotent because external ids upsert.
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

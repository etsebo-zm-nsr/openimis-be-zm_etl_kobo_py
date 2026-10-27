"""Kobo-specific transformation.

Everything expressible as configuration lives in `apps.py`. What remains here is what
config cannot describe: one submission is a whole household, so it has to be exploded
into one row per roster member before the shared mapping can run, and a few member
values (role, date of birth) need more than a path lookup.
"""
import logging

from api_etl.adapters import BaseMappingAdapter

logger = logging.getLogger(__name__)

# Kobo's relationship choices do not say whether a child, grandchild, sibling or parent is
# male or female; openIMIS keeps them apart. The member's gender resolves them.
GENDERED_ROLES = {
    "own_child":     {"MALE": "SON", "FEMALE": "DAUGHTER"},
    "adopted_child": {"MALE": "SON", "FEMALE": "DAUGHTER"},
    "grandchild":    {"MALE": "GRANDSON", "FEMALE": "GRANDDAUGHTER"},
    "brother_sister": {"MALE": "BROTHER", "FEMALE": "SISTER"},
    "parent":        {"MALE": "FATHER", "FEMALE": "MOTHER"},
}

UUID_PREFIX = "uuid:"

# Defaults for adapter.options, so a deployment config that omits one still works.
DEFAULT_ROSTER_FIELD = "consented_grp/household_roster_sec"
DEFAULT_HOUSEHOLD_ID_FIELD = "meta/rootUuid"
DEFAULT_MEMBER_ID_FIELDS = [["_etl_household_id", "_etl_member_position"]]
DEFAULT_HEAD_ONLY_FIELDS = ["phone"]


def _strip_uuid(value):
    """Drop Kobo's leading "uuid:" from an id; None for an absent/blank value."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.startswith(UUID_PREFIX):
        text = text[len(UUID_PREFIX):]
    return text or None


class KoboAdapter(BaseMappingAdapter):
    """Maps KoboToolbox submissions onto the individual import columns.

    A submission is one household whose members are a LIST under the roster key (the v2
    API returns repeat groups as nested arrays, not flattened keys). `transform` turns
    each submission into one row per member, then hands those rows to the shared
    pipeline, so the base class sees an ordinary flat row per person.
    """

    def __init__(self, cfg):
        super().__init__(cfg)
        # Submissions that produced no people (no roster, an empty one, or not a list -
        # in the data, consent = "no"). Not held records: there is no person to hold.
        self.submissions_without_members = 0

    # ---------------------------------------------------------------- options

    def _option(self, name, default):
        options = self.cfg.adapter.options or {}
        value = options.get(name)
        return default if value in (None, "") else value

    # ---------------------------------------------------------------- explode

    def transform(self, data):
        if data is None:
            raise self.Error("Invalid input, expect input not to be None")
        exploded = []
        for submission in data:
            exploded.extend(self._explode(submission))
        if self.submissions_without_members:
            logger.warning(
                "api_etl[%s]: %s submission(s) had no household roster members and "
                "produced no people.", self.cfg.name, self.submissions_without_members)
        return super().transform(exploded)

    def _explode(self, submission):
        """One row per roster member: the household's answers plus the member's own."""
        if not isinstance(submission, dict):
            self.submissions_without_members += 1
            return []
        roster_key = self._option("roster_field", DEFAULT_ROSTER_FIELD)
        roster = self.resolve_path(submission, roster_key)
        if not isinstance(roster, list):
            roster = []
        base = {k: v for k, v in submission.items() if k != roster_key}
        household_id = _strip_uuid(self.resolve_path(
            submission, self._option("household_id_field", DEFAULT_HOUSEHOLD_ID_FIELD)))

        rows = []
        for position, member in enumerate(roster, 1):
            if not isinstance(member, dict):
                continue
            row = {**base, **member,
                   "_etl_household_id": household_id,
                   "_etl_member_position": position}
            row["_etl_member_id"] = self._member_id(row)
            rows.append(row)
        if not rows:
            self.submissions_without_members += 1
        return rows

    def _member_id(self, row):
        """First `member_id_fields` alternative whose parts all resolve, joined with ':'."""
        for alternative in self._option("member_id_fields", DEFAULT_MEMBER_ID_FIELDS):
            parts = [_strip_uuid(self.resolve_path(row, path)) for path in alternative]
            if parts and all(part is not None for part in parts):
                return ":".join(parts)
        return None

    # ---------------------------------------------------------------- roles

    def map_role(self, raw_role):
        """Resolve Kobo's gender-ambiguous relationships, else fall back to the config map."""
        key = str(raw_role).strip().lower() if raw_role not in (None, "") else ""
        if key in GENDERED_ROLES:
            gender = str(self._current_gender or "").strip().upper()
            mapped = GENDERED_ROLES[key].get(gender)
            if mapped:
                return mapped
            # Gender absent or unexpected: a neutral relative is honest, an invented
            # SON-or-DAUGHTER guess is not.
            return self.cfg.adapter.default_role or "OTHER_RELATIVE"
        return super().map_role(raw_role)

    # ---------------------------------------------------------------- dob

    def _dob_path(self):
        return (self.cfg.adapter.field_map or {}).get("dob")

    def _dob_as_sent(self, row):
        """The date of birth exactly as Kobo sent it (whitespace-collapsed), or None."""
        path = self._dob_path()
        raw = self.clean_text(self.resolve_path(row, path)) if path else None
        return None if raw in (None, "") else str(raw)

    def resolve_dob(self, row):
        """The reported date of birth, or None. Never an estimate.

        The registry stores only what the source asserted. `calc_hmember_age` is not a
        date of birth and is not turned into one. An implausible date resolves to None
        here; the raw value is kept as `dob_as_sent` by `transform_row`.
        """
        return self.parse_date(self._dob_as_sent(row))

    # ---------------------------------------------------------------- row

    def transform_row(self, row):
        # map_role needs the member's gender, which the base class does not pass down.
        # Held per row rather than threaded through the signature so the core SPI stays
        # unchanged.
        sex_path = (self.cfg.adapter.field_map or {}).get("sex")
        self._current_gender = self.resolve_path(row, sex_path) if sex_path else None
        try:
            record = super().transform_row(row)
        finally:
            self._current_gender = None
        if record is None:
            return None

        # Store what the source asserted, nothing generated. An unusable date becomes
        # NULL, and the raw value is kept beside it for the data owner.
        record["dob"] = self.resolve_dob(row)
        sent = self._dob_as_sent(row)
        if record["dob"] is None and sent is not None:
            record["dob_as_sent"] = sent
        # year_of_birth feeds the identity key. It is the year of the REPORTED date, a
        # projection of what was sent, not a generated value; no date, no year.
        record["year_of_birth"] = record["dob"][:4] if record["dob"] else None

        # Always positional, even when the member id uses a UUID: it is the handle that
        # lets people imported under a positional id be remapped later.
        household_id = row.get("_etl_household_id")
        record["source_member_ref"] = (
            f"{household_id}:{row.get('_etl_member_position')}" if household_id else None)

        # Household-level answers that belong to the head alone are blanked on everyone
        # else. Set to None rather than removed so the column exists in every row.
        role_field = self.cfg.adapter.role_field
        relationship = self.resolve_path(row, role_field) if role_field else None
        if str(relationship or "").strip().lower() != "head":
            for name in self._option("head_only_fields", DEFAULT_HEAD_ONLY_FIELDS):
                record[name] = None

        return record

    _current_gender = None

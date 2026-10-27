"""Kobo adapter tests: one submission (a household) becomes one record per roster member."""
import copy

from django.test import TestCase

from api_etl.config import build_source_config
from zm_etl_kobo.adapter import KoboAdapter
from zm_etl_kobo.apps import DEFAULT_CONFIG, KOBO_FIELD_MAP, ROSTER

R = ROSTER + "/"
H = "consented_grp/household_identification_sec/"
HH_UUID = "164e6ff8-b3f2-4f7e-8968-17305a6eab32"


def _config(**options):
    """DEFAULT_CONFIG with `adapter.options` entries overridden."""
    raw = copy.deepcopy(DEFAULT_CONFIG)
    raw["adapter"]["options"].update(options)
    return build_source_config("kobo", raw)


def _adapter(**options):
    return KoboAdapter(_config(**options))


def _member(first, last, relationship, gender="male", **extra):
    member = {
        R + "first_name_hmember": first,
        R + "last_name_hmember": last,
        R + "dob_hmember": "1990-04-02",
        R + "gender_hmember": gender,
        R + "relationship_hh_hmember": relationship,
        R + "calc_hmember_age": "36",
    }
    if gender is None:
        del member[R + "gender_hmember"]
    member.update(extra)
    return member


def _submission(members=None, **extra):
    """A synthetic submission in the real shape (invented people)."""
    if members is None:
        members = [
            _member("Chanda", "Mwale", "head", "male",
                    **{R + "nrc_num_hmember": "851324/75/1"}),
            _member("Mutinta", "Mwale", "spouse", "female"),
            _member("Bwalya", "Mwale", "own_child", "male"),
        ]
    submission = {
        "meta/rootUuid": "uuid:" + HH_UUID,
        "_uuid": "11111111-2222-3333-4444-555555555555",
        "_submission_time": "2026-08-20T18:10:49",
        "province_name": "SOUTHERN",
        "district_name": "MAZABUKA",
        "constituency_name": "MAZABUKA CENTRAL",
        "ward_name": "SIYOOWI",
        "enumerator_name": "Enumerator One",
        "enumerator_phone_num": "0970000000",
        "consent_grp/consent": "yes",
        H + "phone_num_hh": "0960000000",
        H + "hsize": "3",
        # The head repeated here must NOT become a fourth person.
        H + "first_name_hh": "Chanda",
        H + "last_name_hh": "Mwale",
        H + "dob_hh": "1990-04-02",
        H + "sex_hh": "male",
        "consented_grp/household_characteristics_sec/roof_type": "iron_sheets",
        "consented_grp/household_characteristics_sec/walls_type": "burnt_bricks",
        "consented_grp/household_characteristics_sec/floor_type": "cement",
        "consented_grp/household_characteristics_sec/main_cooking_energy_type": "firewood",
        "consented_grp/household_characteristics_sec/main_light_energy_type": "solar",
        ROSTER: members,
    }
    submission.update(extra)
    return submission


def _one(adapter, member_overrides=None, **member_kwargs):
    """Transform a single-member household and return its record."""
    member = _member("Test", "Person", **member_kwargs)
    member.update(member_overrides or {})
    out = adapter.transform([_submission([member])])
    assert len(out) == 1, out
    return out[0]


class ExplodeTestCase(TestCase):

    def test_three_member_household_gives_three_records(self):
        adapter = _adapter()
        out = adapter.transform([_submission()])
        self.assertEqual(len(out), 3)
        self.assertEqual([r["first_name"] for r in out], ["Chanda", "Mutinta", "Bwalya"])
        self.assertEqual(adapter.skipped, [])
        self.assertEqual(adapter.submissions_without_members, 0)

    def test_head_comes_from_the_roster_only(self):
        # Section 1 repeats the head's name; if it were imported there would be 4 people.
        out = _adapter().transform([_submission()])
        self.assertEqual(sum(1 for r in out if r["household_role"] == "HEAD"), 1)
        self.assertEqual(len(out), 3)

    def test_all_members_share_one_group_code(self):
        out = _adapter().transform([_submission()])
        self.assertEqual({r["group_code"] for r in out}, {"zm-hh-kobo:" + HH_UUID})

    def test_two_households_get_distinct_group_codes(self):
        other = _submission(**{"meta/rootUuid": "uuid:aaaaaaaa-0000-0000-0000-000000000000"})
        out = _adapter().transform([_submission(), other])
        self.assertEqual(len(out), 6)
        self.assertEqual(len({r["group_code"] for r in out}), 2)
        self.assertEqual(len({r["external_id"] for r in out}), 6)

    def test_household_fields_are_copied_onto_every_member(self):
        for record in _adapter().transform([_submission()]):
            self.assertEqual(record["district"], "MAZABUKA")
            self.assertEqual(record["roof_material"], "iron_sheets")
            self.assertEqual(record["registered_at"], "2026-08-20T18:10:49")
            self.assertEqual(record["informed_consent"], "yes")

    def test_input_submission_is_not_mutated(self):
        submission = _submission()
        before = copy.deepcopy(submission)
        _adapter().transform([submission])
        self.assertEqual(submission, before)


class NoMembersTestCase(TestCase):

    def test_empty_missing_and_non_list_rosters_give_no_records(self):
        no_roster = _submission()
        del no_roster[ROSTER]
        null_roster = _submission(**{ROSTER: None})
        data = [
            _submission([]),
            no_roster,
            _submission("not a list"),
            null_roster,
        ]
        adapter = _adapter()
        self.assertEqual(adapter.transform(data), [])
        self.assertEqual(adapter.submissions_without_members, 4)
        # Not held records: there is no person to hold.
        self.assertEqual(adapter.skipped, [])
        self.assertEqual(adapter.held_rows, [])

    def test_empty_submissions_do_not_disturb_the_others(self):
        adapter = _adapter()
        out = adapter.transform([_submission([]), _submission()])
        self.assertEqual(len(out), 3)
        self.assertEqual(adapter.submissions_without_members, 1)

    def test_warning_is_logged_once(self):
        with self.assertLogs("zm_etl_kobo.adapter", level="WARNING") as logs:
            _adapter().transform([_submission([]), _submission([])])
        self.assertEqual(len(logs.records), 1)
        self.assertIn("2 submission(s)", logs.output[0])

    def test_no_warning_when_every_submission_has_members(self):
        with self.assertNoLogs("zm_etl_kobo.adapter", level="WARNING"):
            _adapter().transform([_submission()])


class IdentifiersTestCase(TestCase):

    def test_external_ids_are_positional_and_uuid_prefix_is_stripped(self):
        out = _adapter().transform([_submission()])
        self.assertEqual([r["external_id"] for r in out],
                         [f"kobo:{HH_UUID}:{n}" for n in (1, 2, 3)])
        for record in out:
            self.assertNotIn("uuid:", record["external_id"])
            self.assertNotIn("uuid:", record["group_code"])

    def test_source_member_ref_is_positional(self):
        out = _adapter().transform([_submission()])
        self.assertEqual([r["source_member_ref"] for r in out],
                         [f"{HH_UUID}:{n}" for n in (1, 2, 3)])

    def test_unresolvable_household_id_skips_the_members(self):
        submission = _submission()
        del submission["meta/rootUuid"]
        adapter = _adapter()
        with self.assertLogs("api_etl.adapters.base_mapping_adapter", level="WARNING"):
            out = adapter.transform([submission])
        self.assertEqual(out, [])

    def test_member_uuid_is_used_when_configured_with_positional_fallback(self):
        members = [
            _member("Chanda", "Mwale", "head",
                    **{R + "member_uuid": "uuid:aaaa1111-0000-0000-0000-000000000001"}),
            _member("Mutinta", "Mwale", "spouse", "female"),      # no member_uuid
            _member("Bwalya", "Mwale", "own_child",
                    **{R + "member_uuid": "bbbb2222-0000-0000-0000-000000000002"}),
        ]
        adapter = _adapter(member_id_fields=[
            [R + "member_uuid"], ["_etl_household_id", "_etl_member_position"]])
        out = adapter.transform([_submission(members)])
        self.assertEqual(out[0]["external_id"], "kobo:aaaa1111-0000-0000-0000-000000000001")
        self.assertEqual(out[1]["external_id"], f"kobo:{HH_UUID}:2")
        self.assertEqual(out[2]["external_id"], "kobo:bbbb2222-0000-0000-0000-000000000002")
        # source_member_ref stays positional for all three.
        self.assertEqual([r["source_member_ref"] for r in out],
                         [f"{HH_UUID}:{n}" for n in (1, 2, 3)])

    def test_composite_alternative_needs_every_part(self):
        # The first alternative has a part that never resolves, so it is skipped entirely
        # rather than producing a half-built id like "kobo:<hh>:None".
        adapter = _adapter(member_id_fields=[
            ["_etl_household_id", R + "no_such_field"],
            ["_etl_household_id", "_etl_member_position"]])
        out = adapter.transform([_submission()])
        self.assertEqual(out[0]["external_id"], f"kobo:{HH_UUID}:1")

    def test_no_alternative_resolving_skips_the_row(self):
        adapter = _adapter(member_id_fields=[[R + "no_such_field"]])
        with self.assertLogs("api_etl.adapters.base_mapping_adapter", level="WARNING"):
            out = adapter.transform([_submission()])
        self.assertEqual(out, [])


class RolesTestCase(TestCase):

    def _role(self, relationship, gender):
        return _one(_adapter(), relationship=relationship, gender=gender)["individual_role"]

    def test_gendered_relationships_resolve_with_the_members_gender(self):
        cases = [
            ("own_child", "male", "SON"), ("own_child", "female", "DAUGHTER"),
            ("adopted_child", "male", "SON"), ("adopted_child", "female", "DAUGHTER"),
            ("grandchild", "male", "GRANDSON"), ("grandchild", "female", "GRANDDAUGHTER"),
            ("brother_sister", "male", "BROTHER"), ("brother_sister", "female", "SISTER"),
            ("parent", "male", "FATHER"), ("parent", "female", "MOTHER"),
        ]
        for relationship, gender, expected in cases:
            self.assertEqual(self._role(relationship, gender), expected,
                             (relationship, gender))

    def test_missing_or_odd_gender_falls_back_to_other_relative(self):
        for relationship in ("own_child", "adopted_child", "grandchild",
                             "brother_sister", "parent"):
            self.assertEqual(self._role(relationship, None), "OTHER_RELATIVE", relationship)
            self.assertEqual(self._role(relationship, "other"), "OTHER_RELATIVE",
                             relationship)

    def test_ungendered_relationships_come_from_the_role_map(self):
        cases = {
            "head": "HEAD", "spouse": "SPOUSE", "step_child": "OTHER_RELATIVE",
            "cousin": "OTHER_RELATIVE", "niece_nephew": "OTHER_RELATIVE",
            "brother_sister_in_law": "OTHER_RELATIVE", "parent_in_law": "OTHER_RELATIVE",
            "other_relative": "OTHER_RELATIVE", "non_relative": "NOT_RELATED",
        }
        for relationship, expected in cases.items():
            self.assertEqual(self._role(relationship, "female"), expected, relationship)

    def test_unknown_relationship_uses_the_default_role(self):
        self.assertEqual(self._role("something_new", "male"), "OTHER_RELATIVE")

    def test_household_role_copy_is_present(self):
        record = _one(_adapter(), relationship="own_child", gender="female")
        self.assertEqual(record["household_role"], "DAUGHTER")

    def test_gender_does_not_leak_between_rows(self):
        members = [_member("A", "B", "own_child", "female"),
                   _member("C", "D", "own_child", None)]
        out = _adapter().transform([_submission(members)])
        self.assertEqual([r["individual_role"] for r in out], ["DAUGHTER", "OTHER_RELATIVE"])


class RecipientAndHeadOnlyTestCase(TestCase):

    def test_head_is_the_recipient_and_others_are_not(self):
        out = _adapter().transform([_submission()])
        self.assertEqual([r["recipient_info"] for r in out], ["1", None, None])
        self.assertEqual([r["household_recipient"] for r in out], ["1", None, None])

    def test_phone_is_kept_on_the_head_only(self):
        out = _adapter().transform([_submission()])
        self.assertEqual(out[0]["phone"], "0960000000")
        self.assertIsNone(out[1]["phone"])
        self.assertIsNone(out[2]["phone"])

    def test_phone_key_exists_on_every_record(self):
        for record in _adapter().transform([_submission()]):
            self.assertIn("phone", record)

    def test_head_only_fields_is_configurable(self):
        out = _adapter(head_only_fields=["phone", "roof_material"]).transform(
            [_submission()])
        self.assertEqual(out[0]["roof_material"], "iron_sheets")
        self.assertIsNone(out[1]["roof_material"])
        out = _adapter(head_only_fields=[]).transform([_submission()])
        self.assertEqual({r["phone"] for r in out}, {"0960000000"})

    def test_head_only_matches_on_the_relationship_not_the_position(self):
        # The head need not be first in the roster.
        members = [_member("A", "B", "spouse", "female"), _member("C", "D", "head")]
        out = _adapter().transform([_submission(members)])
        self.assertIsNone(out[0]["phone"])
        self.assertEqual(out[1]["phone"], "0960000000")


class DobTestCase(TestCase):

    def test_valid_dob_gives_dob_and_year_of_birth(self):
        record = _one(_adapter(), relationship="head",
                      member_overrides={R + "dob_hmember": "2005-11-07"})
        self.assertEqual(record["dob"], "2005-11-07")
        self.assertEqual(record["year_of_birth"], "2005")
        self.assertNotIn("dob_as_sent", record)

    def test_unusable_dob_is_null_and_raw_value_kept(self):
        for raw in ("1850-01-01", "abc", "2999-01-01"):
            record = _one(_adapter(), relationship="head",
                          member_overrides={R + "dob_hmember": raw})
            self.assertIsNone(record["dob"], raw)
            self.assertEqual(record["dob_as_sent"], raw)
            self.assertIsNone(record["year_of_birth"], raw)

    def test_absent_dob_gives_null_without_dob_as_sent(self):
        for value in (None, "", "  "):
            record = _one(_adapter(), relationship="head",
                          member_overrides={R + "dob_hmember": value})
            self.assertIsNone(record["dob"])
            self.assertIsNone(record["year_of_birth"])
            self.assertNotIn("dob_as_sent", record)

    def test_age_never_produces_a_dob(self):
        member = _member("A", "B", "head")
        del member[R + "dob_hmember"]
        record = _adapter().transform([_submission([member])])[0]
        self.assertIsNone(record["dob"])
        self.assertEqual(record["age"], "36")

    def test_member_without_dob_is_still_imported(self):
        member = _member("A", "B", "head")
        del member[R + "dob_hmember"]
        adapter = _adapter()
        self.assertEqual(len(adapter.transform([_submission([member])])), 1)
        self.assertEqual(adapter.skipped, [])


class NationalIdTestCase(TestCase):

    def test_nrc_is_normalised(self):
        out = _adapter().transform([_submission()])
        self.assertEqual(out[0]["national_id"], "851324751")

    def test_absent_nrc_is_none(self):
        out = _adapter().transform([_submission()])
        self.assertIsNone(out[1]["national_id"])
        self.assertIsNone(out[2]["national_id"])


class IdentityTestCase(TestCase):

    def test_identity_key_is_built_from_name_year_sex_district(self):
        out = _adapter().transform([_submission()])
        self.assertTrue(all(r.get("identity_key") for r in out))
        self.assertEqual(len({r["identity_key"] for r in out}), 3)

    def test_no_dob_means_no_identity_key(self):
        member = _member("A", "B", "head")
        del member[R + "dob_hmember"]
        out = _adapter().transform([_submission([member])])
        self.assertNotIn("identity_key", out[0])


class DefaultConfigTestCase(TestCase):

    def test_every_field_map_path_resolves_on_the_fixture(self):
        # Guards typos in the paths: each one must find a value on a full member row.
        member = _member(
            "A", "B", "head",
            **{R + "marital_status_hmember": "married",
               R + "birth_certificate_hmember_num": "BC1",
               R + "education_completed": "primary",
               R + "school_attendance_status": "attending",
               R + "member_disability": "no",
               R + "nrc_num_hmember": "851324/75/1",
               R + "enrolled_sec/school_name": "Mazabuka Primary"})
        adapter = _adapter()
        row = adapter._explode(_submission([member]))[0]
        for target, path in KOBO_FIELD_MAP.items():
            self.assertIsNotNone(adapter.resolve_path(row, path),
                                 f"{target} <- {path} does not resolve")
        self.assertIsNotNone(adapter.resolve_path(row, adapter.cfg.adapter.national_id_field))
        self.assertIsNotNone(adapter.resolve_path(row, adapter.cfg.adapter.role_field))

    def test_roster_prefix_in_paths_is_the_configured_roster_field(self):
        options = _config().adapter.options
        self.assertEqual(options["roster_field"], ROSTER)

    def test_options_survive_config_building(self):
        options = _config().adapter.options
        self.assertEqual(options["household_id_field"], "meta/rootUuid")
        self.assertEqual(options["member_id_fields"],
                         [["_etl_household_id", "_etl_member_position"]])
        self.assertEqual(options["head_only_fields"], ["phone"])

    def test_extra_columns_declared(self):
        self.assertEqual(_config().adapter.extra_columns,
                         ["dob_as_sent", "year_of_birth", "source_member_ref"])

    def test_every_emitted_column_is_known(self):
        # Every key a record carries must be a field_map target, a base-class column, or a
        # declared extra column - an undeclared one makes the whole upload be rejected.
        cfg = _config()
        known = set(KOBO_FIELD_MAP) | set(cfg.adapter.extra_columns)
        for record in _adapter().transform([_submission()]):
            emitted = set(record) - known
            self.assertLessEqual(emitted, {
                "external_id", "group_code", "household_ref", "individual_role",
                "household_role", "recipient_info", "household_recipient",
                "national_id", "beneficiary_data_source", "identity_key",
                "location_match", "source_location", "location_code",
                "name_quality", "text_quality"}, emitted)

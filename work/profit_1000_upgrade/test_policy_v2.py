import unittest

from work.profit_1000_upgrade import policy_v2 as policy


class PolicyV2Tests(unittest.TestCase):
    def test_json_copy_is_not_the_registry(self):
        value = policy.source_policy_contract()
        self.assertEqual(policy.validate_contract(value, exact=True), value)
        value["entry_policy_id"] = "other"
        self.assertEqual(policy.CONTRACT["entry_policy_id"], policy.ENTRY_POLICY_ID)
        with self.assertRaises(TypeError):
            policy.CONTRACT["entry_policy_id"] = "other"

    def test_missing_or_changed_fields_are_rejected(self):
        for field in policy.CONTRACT:
            for mutation in (None, "other", False):
                with self.subTest(field=field, mutation=mutation):
                    value = policy.source_policy_contract()
                    value[field] = mutation
                    with self.assertRaises(ValueError):
                        policy.validate_contract(value)

    def test_nested_policy_must_match(self):
        row = {**policy.source_policy_contract(), "source_policy_contract": policy.source_policy_contract()}
        policy.validate_contract(row)
        row["source_policy_contract"]["minute_time_semantics"] = "PROVIDER_CONFIRMED"
        with self.assertRaises(ValueError):
            policy.validate_contract(row)

    def test_standalone_rejects_extra_fields_but_rows_allow_them(self):
        row = {**policy.source_policy_contract(), "signal_date": "20260814"}
        policy.validate_contract(row)
        with self.assertRaises(ValueError):
            policy.validate_contract(row, exact=True)

    def test_recognition_is_not_validation(self):
        row = {"entry_policy_id": policy.ENTRY_POLICY_ID}
        self.assertTrue(policy.has_v2_policy(row))
        with self.assertRaises(ValueError):
            policy.validate_contract(row)

    def test_only_evidenced_no_fill_states_can_be_terminal(self):
        for status in policy.NO_FILL_STATUSES:
            row = dict(policy.CONTRACT, label_status=status, minute_source_observed=False)
            policy.validate_label_contract(row)
        for status in ("NO_FILL_API_ERROR", "NO_FILL_SOURCE_UNAVAILABLE", "NO_FILL_PRICE_MISMATCH",
                       "NO_FILL_ABOVE_FROZEN_CAP", "NO_FILL_UNKNOWN"):
            with self.subTest(status=status):
                row = dict(policy.CONTRACT, label_status=status, minute_source_observed=False)
                with self.assertRaises(ValueError):
                    policy.validate_label_contract(row)


if __name__ == "__main__":
    unittest.main()

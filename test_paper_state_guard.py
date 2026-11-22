import json
import unittest

from paper_state_guard import paper_trade_count, should_restore_paper_state, should_upload_paper_state


def state(count):
    return json.dumps({"trades": [{"id": i} for i in range(count)]}).encode()


class PaperStateGuardTests(unittest.TestCase):
    def test_counts_valid_trade_history(self):
        self.assertEqual(paper_trade_count(state(3)), 3)

    def test_rejects_invalid_or_missing_trade_list(self):
        self.assertIsNone(paper_trade_count(b"{}"))
        self.assertIsNone(paper_trade_count(b"not json"))

    def test_empty_remote_cannot_replace_nonempty_local(self):
        self.assertFalse(should_restore_paper_state(state(49), state(0)))

    def test_shorter_remote_cannot_replace_more_complete_local(self):
        self.assertFalse(should_restore_paper_state(state(50), state(49)))

    def test_valid_remote_restores_when_local_missing(self):
        self.assertTrue(should_restore_paper_state(None, state(49)))

    def test_invalid_remote_is_not_restored(self):
        self.assertFalse(should_restore_paper_state(state(2), b"bad json"))

    def test_empty_local_cannot_overwrite_nonempty_remote(self):
        self.assertFalse(should_upload_paper_state(state(0), state(49)))

    def test_shorter_local_cannot_overwrite_remote(self):
        self.assertFalse(should_upload_paper_state(state(48), state(49)))

    def test_equal_or_larger_local_history_can_upload(self):
        self.assertTrue(should_upload_paper_state(state(49), state(49)))
        self.assertTrue(should_upload_paper_state(state(50), state(49)))

    def test_new_file_can_be_uploaded(self):
        self.assertTrue(should_upload_paper_state(state(0), None))

    def test_invalid_local_is_never_uploaded(self):
        self.assertFalse(should_upload_paper_state(b"bad json", state(49)))


if __name__ == "__main__":
    unittest.main()

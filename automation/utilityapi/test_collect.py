import unittest
from datetime import datetime
from collect import same_month, already_collected, ZONE

class CollectionGuardTests(unittest.TestCase):
    def test_los_angeles_month_boundary(self):
        now = datetime(2026, 10, 15, tzinfo=ZONE)
        self.assertFalse(same_month('2026-10-01T06:59:00Z', now))
        self.assertTrue(same_month('2026-10-01T07:00:00Z', now))
    def test_discovery_is_not_collection(self):
        now = datetime(2026, 10, 15, tzinfo=ZONE)
        self.assertFalse(already_collected({'notes':[{'type':'meters_full','ts':now.isoformat()}]}, now))
    def test_successful_and_partial_collection_skip(self):
        now = datetime(2026, 10, 15, tzinfo=ZONE)
        for kind in ['bills_full','intervals_full','intervals_mixed','bills_partial']:
            self.assertTrue(already_collected({'notes':[{'type':kind,'ts':now.isoformat()}]}, now))
    def test_bad_timestamp_does_not_claim_success(self):
        self.assertFalse(same_month(None, datetime.now(ZONE)))

if __name__ == '__main__': unittest.main()

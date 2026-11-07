import unittest
from module.fleet_scheduler.scheduler import classify_stage_fleet_mode, StageFleetMode

class TestClassifier(unittest.TestCase):
    def test_main_hard_dedicated(self):
        self.assertEqual(
            classify_stage_fleet_mode('campaign_main', '12-2', 'hard'),
            StageFleetMode.DEDICATED_SLOT)
    def test_main_normal_preset(self):
        self.assertEqual(
            classify_stage_fleet_mode('campaign_main', '12-2', 'normal'),
            StageFleetMode.PRESET_FORMATION)
    def test_event_t1_preset(self):
        self.assertEqual(
            classify_stage_fleet_mode('event_xxx', 't1', 'normal'),
            StageFleetMode.PRESET_FORMATION)
    def test_event_t4_dedicated(self):
        self.assertEqual(
            classify_stage_fleet_mode('event_xxx', 't4', 'hard'),
            StageFleetMode.DEDICATED_SLOT)
    def test_sp_dedicated(self):
        self.assertEqual(
            classify_stage_fleet_mode('event_xxx', 'sp', 'normal'),
            StageFleetMode.DEDICATED_SLOT)
if __name__ == '__main__':
    unittest.main()

import unittest
import inspect
from module.os.map import OSMap
class TestFog(unittest.TestCase):
    def test_fog_offset(self):
        src = inspect.getsource(OSMap)
        self.assertIn('200, 5', src)
        self.assertIn('threshold=40', src)
    def test_stuck_clear_not_gated(self):
        src = inspect.getsource(OSMap)
        self.assertIn('stuck_record_clear()', src)
        self.assertNotIn('gated by ON', src)
if __name__ == '__main__':
    unittest.main()

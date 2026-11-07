import unittest
KNOWN = {'DAILY RESOURCE EXTRACTION', 'URGENT', 'COMBAT', 'RESOURCE'}
def parse_genre(text):
    text = text.strip().upper()
    if text in KNOWN: return text
    return None
class TestGenre(unittest.TestCase):
    def test_known_genres(self):
        self.assertEqual(parse_genre('DAILY RESOURCE EXTRACTION'), 'DAILY RESOURCE EXTRACTION')
    def test_reject_garbage(self):
        self.assertIsNone(parse_genre('CK UAL SJI'))
        self.assertIsNone(parse_genre('{警}'))
if __name__ == '__main__':
    unittest.main()

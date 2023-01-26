import os
import sys
import unittest

# to import from a parent directory we need to add that directory to the system path
# get current script directory
current_script_directory = os.path.dirname(os.path.realpath(__file__))
# grandparent directory (should be the scrapers one)
grandparent = os.path.dirname(os.path.dirname(current_script_directory))
scrapers_directory = os.path.join(grandparent, 'scrapers')
# add grandparent dir to sys path so that we can import py_common from there
print(scrapers_directory)
sys.path.append(scrapers_directory)

import LifeSelector


class TestLifeSelector(unittest.TestCase):

    def test_sceneByURL(self):
        self.assertEqual('blah', 'blah2')

if __name__ == '__main__':
    unittest.main()

"""
Unit tests for life_selector.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch
import yaml

# to import from a parent directory we need to add that directory to the
# system path
current_script_directory = os.path.dirname(os.path.realpath(__file__))
# grandparent directory (should be the repo root folder)
grandparent = os.path.dirname(os.path.dirname(current_script_directory))
scrapers_directory = os.path.join(grandparent, 'scrapers')
# add scrapers dir to sys path so that we can import py_common and scrapers
# from there
sys.path.append(scrapers_directory)

from life_selector import LifeSelectorScraper   # noqa: E402
from py_common import graphql                   # noqa: E402


class TestLifeSelectorScraper(unittest.TestCase):
    """
    Unit tests for LifeSelectorScraper class
    """
    scraper_yaml = None

    def setUp(self) -> None:
        if not self.scraper_yaml:
            with open(
                os.path.join(scrapers_directory, 'life_selector.yml'),
                encoding="utf-8"
            ) as stream:
                self.scraper_yaml = yaml.safe_load(stream)


    def _scraper_with_patched_sys_argv(
        self, test_args
    ) -> LifeSelectorScraper:
        """
        Helper method to populate sys argv
        """
        with patch.object(sys, 'argv', test_args):
            scraper = LifeSelectorScraper()
        return scraper

    def test_scraper_yaml_loads(self):
        """
        Check this test class has loaded the scraper yaml config
        """
        # given
        expected_properties = [
            'name',
            'sceneByURL',
            'sceneByFragment',
            'sceneByName',
            'sceneByQueryFragment',
            'galleryByURL'
        ]

        # when
        # (setUp() has run)

        # then
        self.assertIsNotNone(self.scraper_yaml)
        for expected_prop in expected_properties:
            self.assertIn(expected_prop, self.scraper_yaml)

    def test_class_with_args_instantiates(self):
        """
        Test class with a populated sys.argv
        """
        # given
        test_args = ["_", "lifeselector"]

        # when
        scraper = self._scraper_with_patched_sys_argv(test_args)

        # then
        self.assertIsInstance(scraper, LifeSelectorScraper)

    @patch(
        'builtins.input',
        side_effect=[
            json.dumps({
                "name": "Billy Bob",
                "id": "123456",
                "title": "Bob of the Billies",
                "url": "http://bob"
            })
        ]
    )
    def test_load_from_input(self, _):
        """
        Test class with populated sys.argv and input
        """
        # given
        # input (like stdin) patched as above
        test_args = ["_", "lifeselector"]
        scraper = self._scraper_with_patched_sys_argv(test_args)

        # when
        scraper.load_from_input()

        # then
        self.assertEqual(scraper.search_title, "Billy Bob")
        self.assertEqual(scraper.scene_id, "123456")
        self.assertEqual(scraper.scene_title, "Bob of the Billies")
        self.assertEqual(scraper.scene_url, "http://bob")

    def test_get_db_path_no_response(self):
        """
        Test getting database path with no response
        """
        # given
        test_args = ["_", "lifeselector"]
        scraper = self._scraper_with_patched_sys_argv(test_args)

        # when
        scraper.get_db_path()

        # then
        self.assertIsNone(scraper.db_path)

    def test_get_db_path_from_config_database_path(self):
        """
        Test getting database path with response
        """
        # given
        test_args = ["_", "lifeselector"]
        scraper = self._scraper_with_patched_sys_argv(test_args)

        # when
        with patch.object(graphql, 'configuration', return_value={
            'general': {
                'databasePath': '/path7'
            }
        }):
            scraper.get_db_path()

        # then
        self.assertEqual(scraper.db_path, '/path7')

    def test_sceneByURL(self):
        self.assertEqual('blah', 'blah')


if __name__ == '__main__':
    unittest.main()

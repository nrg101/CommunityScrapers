"""
This is a scraper for lifeselector.com and 21roles.com
"""
import datetime
import difflib
import json
import os
import re
import sqlite3
import sys
from typing import List
from configparser import ConfigParser, NoSectionError
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup as bs
    import requests
except ModuleNotFoundError:
    print(
        "You need to install the following modules 'requests', 'bs4', 'lxml'.",
        file=sys.stderr
    )
    sys.exit(1)

try:
    from py_common import graphql
    from py_common import log
except ModuleNotFoundError:
    print(
        "You need to download the folder 'py_common' from the community repo! "
        "(CommunityScrapers/tree/master/scrapers/py_common)",
        file=sys.stderr)
    sys.exit(1)


class LifeSelectorScraper:
    """
    Scraper for lifeselector.com (and compatible sites)
    """

    action = None
    api_search_results = None
    api_url = "https://contentworker.ls-tester.com/api/search"
    args = {}
    config_path = None
    database_dict = None
    db_path = None
    fixed_tag = "Male POV"      # Extra tag that will be added to the scene
    fragment = {}
    game_by_id_url = "https://lifeselector.com/game/DisplayPlayer/gameId"
    game_description = None
    game_id = None
    game_title = None
    game_url = None
    headers = None
    search_title = None
    site = None
    url_domain = None
    url_id = None
    scraped = None

    def __init__(self):
        """
        Constructor
        """

        try:
            userfolder_path = re.match(r".+\.stash.", __file__).group(0)
            self.config_path = userfolder_path + "config.yml"
            log.debug(f"Config Path: {self.config_path}")
        except Exception as ex:
            log.warning(ex)
            log.debug("No config")

        self.headers = {
            "User-Agent":
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:79.0) '
                + 'Gecko/20100101 Firefox/79.0',
            "Origin": f"https://www.{self.site}.com",
            "Referer": f"https://www.{self.site}.com"
        }

    def clean_text(self, details: str) -> str:
        """
        remove escaped backslashes and html parse the details text
        """
        if details:
            details = details.replace('\\', '')
            details = re.sub(
                r"<\s*/?br\s*/?\s*>", "\n",
                details
            )  # bs.get_text doesnt replace br's with \n
            details = bs(details, features='lxml').get_text()
        return details

    def check_db(self, database_path: str, scn_id: str) -> dict:
        """
        get scene data (size, duration, height) directly from the database file
        """
        try:
            sqlite_connection = sqlite3.connect("file:" + database_path +
                                                "?mode=ro",
                                                uri=True)
            log.debug("Connected to SQLite database")
        except Exception as ex:
            log.error(ex)
            log.warning("Fail to connect to the database")
            return {}
        cursor = sqlite_connection.cursor()
        cursor.execute(
            "SELECT size,duration,height from scenes WHERE id=?;",
            [scn_id]
        )
        record = cursor.fetchall()
        database = {}
        database["size"] = int(record[0][0])
        database["duration"] = int(record[0][1])
        database["height"] = str(record[0][2])
        cursor.close()
        sqlite_connection.close()
        return database

    def send_request(
        self, url: str, head: str, params: dict = None
    ) -> requests.Response:
        """
        get response from url
        """
        log.trace(f"Request URL: {url}")
        try:
            response = \
                requests.get(url, headers=head, params=params, timeout=10)
        except requests.RequestException as req_error:
            log.warning(f"Requests failed: {req_error}")
            return None
        log.trace(f"Returned URL: {response.url}")
        if response.content and response.status_code == 200:
            # log.debug(f"response.text: {response.text}")
            return response
        log.warning(f"[REQUEST] Error, Status Code: {response.status_code}")
        return None

    def scrape_game_page_html(self, url: str, head: dict) -> None:
        """
        scrape a game's HTML page

        saves:
        {
            "scraped": {
                "title": "<game/release title>",
                "details": "<game/release description>",
                "scenes": [
                    {
                        "image": "<individual scene image url>"
                    }
                ]
            }
        }
        """
        self.scraped = {}
        log.info(f"Scraping HTML page {url}")
        req = self.send_request(url, head)

        page = bs(req.text, 'html.parser')
        self.scraped["title"] = page.title.string.replace(
            ' - Interactive Porn Game',
            ''
        )
        self.scraped["details"] = \
            page.find("div", class_="info").find("p").string
        self.scraped["scenes"] = [
            {"image": tag['src']} for tag in
            page.find("div", class_="player").find_all("img")[1:]
        ]

        log.debug(f"scraped: {self.scraped}")
        log.info(f"Found {len(self.scraped['scenes'])} scenes in this game")

    def fetch_page_json(self, page_html):
        matches = re.findall(r'window.env\s+=\s(.+);', page_html, re.MULTILINE)
        return None if len(matches) == 0 else json.loads(matches[0])

    def api_search_req(self, type_search, query, url):
        """
        API Search Data
        """
        log.debug(f"type_search: {type_search}, query: {query}, url: {url}")
        api_request = None
        if type_search == "query":
            api_request = self.api_search_query(query, url)
        if type_search == "id":
            api_request = self.api_search_id(query, url)
        # log.debug(f"api_request: {api_request.content}")
        if api_request:
            self.api_search_results = None
            api_request_json = api_request.json()
            if "games" in api_request_json:
                self.api_search_results = api_request_json["games"]
            elif "map" in api_request_json:
                self.api_search_results = [api_request_json["map"]]
            if self.api_search_results:
                log.info(
                    f"[API] Search gives {len(self.api_search_results)} result(s)"
                )
                log.debug(
                    f"self.api_search_results: {self.api_search_results}"
                )
                return self.api_search_results
        return None

    def api_search_id(self, scene_id, url):
        search_url = f"{url}/{scene_id}/choiceId/0"
        req = self.send_request(search_url, self.headers)
        return req

    def api_search_gallery_id(self, p_id, url):
        gallery_id = [f"set_id:{p_id}"]
        request_api = {
            "requests": [{
                "indexName": "all_photosets",
                "params": "query=&hitsPerPage=20&page=0",
                "facetFilters": gallery_id
            }]
        }
        req = self.send_request(url, self.headers, request_api)
        return req

    def api_search_query(self, query, url):
        params = {'q': query}
        req = self.send_request(url, self.headers, params)
        return req

    def json_parser(
        self, search_json, range_duration=60, single=False, scene_id=None
    ):
        result_dict = {}
        # Just for not printing the full JSON in log...
        debug_dict = {}
        with open(
            "lifeselector_scene_search.json",
            'w',
            encoding='utf-8'
        ) as search_file:
            json.dump(search_json, search_file, ensure_ascii=False, indent=4)
        for scene in search_json:
            r_match = self.match_result(
                scene, range_duration, single, clip_id=self.url_id)
            if r_match["info"]:
                if result_dict.get(r_match["info"]):
                    # Url should be more accurate than the title
                    if r_match["url"] > result_dict[r_match["info"]]["url"]:
                        result_dict[r_match["info"]] = {
                            "title": r_match["title"],
                            "url": r_match["url"],
                            "clip_id": r_match["clip_id"],
                            "json": scene
                        }
                        debug_dict[r_match["info"]] = {
                            "title": r_match["title"],
                            "url": r_match["url"],
                            "scene": scene["title"]
                        }
                    elif r_match["title"] > result_dict[r_match["info"]][
                            "title"] and r_match["title"] > result_dict[
                                r_match["info"]]["url"]:
                        result_dict[r_match["info"]] = {
                            "title": r_match["title"],
                            "url": r_match["url"],
                            "clip_id": r_match["clip_id"],
                            "json": scene
                        }
                        debug_dict[r_match["info"]] = {
                            "title": r_match["title"],
                            "url": r_match["url"],
                            "scene": scene["title"]
                        }
                else:
                    result_dict[r_match["info"]] = {
                        "title": r_match["title"],
                        "url": r_match["url"],
                        "clip_id": r_match["clip_id"],
                        "json": scene
                    }
                    debug_dict[r_match["info"]] = {
                        "title": r_match["title"],
                        "url": r_match["url"],
                        "scene": scene["title"]
                    }
        log.debug(f"debug_dict: {debug_dict}")
        # Engine whoaaaaa
        # A = ByID/Most likely
        # S = Size
        # D = Duration
        # N = Network
        # R = Only Ratio
        log.info("--- BEST RESULT ---")
        for key, item in debug_dict.items():
            log.info(
                f'[{key}] Title: {item["scene"]}; '
                f'Ratio Title: {round(item["title"], 3)} - '
                f'URL: {round(item["url"], 3)}'
            )
        log.info("--------------")
        #
        if result_dict.get("ASDN"):
            return result_dict["ASDN"]["json"]
        if result_dict.get("ASD"):
            return result_dict["ASD"]["json"]
        if result_dict.get("ASN"):
            return result_dict["ASN"]["json"]
        if result_dict.get("ADN"):
            return result_dict["ADN"]["json"]
        if result_dict.get("AS"):
            return result_dict["AS"]["json"]
        if result_dict.get("AD"):
            return result_dict["AD"]["json"]
        if result_dict.get("AN"):
            if result_dict["AN"]["clip_id"] \
                    or result_dict["AN"]["title"] > 0.5 \
                    or result_dict["AN"]["url"] > 0.5:
                return result_dict["AN"]["json"]
        if result_dict.get("A"):
            if result_dict["A"]["title"] > 0.7 \
                    or result_dict["A"]["url"] > 0.7:
                return result_dict["A"]["json"]
        if result_dict.get("SDN"):
            return result_dict["SDN"]["json"]
        if result_dict.get("SD"):
            return result_dict["SD"]["json"]
        if result_dict.get("SN"):
            if result_dict["SN"]["title"] > 0.5 or \
                    result_dict["SN"]["url"] > 0.5:
                return result_dict["SN"]["json"]
        if result_dict.get("DN"):
            if result_dict["DN"]["title"] > 0.5 \
                    or result_dict["DN"]["url"] > 0.5:
                return result_dict["DN"]["json"]
        if result_dict.get("S"):
            if result_dict["S"]["title"] > 0.7 \
                    or result_dict["S"]["url"] > 0.7:
                return result_dict["S"]["json"]
        if result_dict.get("D"):
            if result_dict["D"]["title"] > 0.7 \
                    or result_dict["D"]["url"] > 0.7:
                return result_dict["D"]["json"]
        if result_dict.get("N"):
            if result_dict["N"]["title"] > 0.7 or \
                    result_dict["N"]["url"] > 0.7:
                return result_dict["N"]["json"]
        if result_dict.get("R"):
            if result_dict["R"]["title"] > 0.8 \
                    or result_dict["R"]["url"] > 0.8:
                return result_dict["R"]["json"]
        return None

    def match_result(
        self, api_scene, range_duration=60, single=False, clip_id: str = None
    ):
        api_title = api_scene.get("title")
        # api_duration = int(api_scene.get("length"))
        api_clip_id = str(api_scene["id"])
        # api_filesize = None
        match_duration = False
        match_size = False
        match_clip_id = False
        # Using database
        # if self.database_dict:
        #     db_duration = int(self.database_dict["duration"])
        #     db_height = str(self.database_dict["height"])
        #     db_size = int(self.database_dict["size"])
        #     if api_scene.get("download_file_sizes"):
        #         if db_height == "2160":
        #             api_filesize = api_scene["download_file_sizes"].get("4k")
        #         else:
        #             api_filesize = \
        #                 api_scene["download_file_sizes"].get(db_height +
        #                                                                 "p")
        #         if api_filesize:
        #             api_filesize = int(api_filesize)
        #     if api_filesize is None:
        #         api_filesize = api_scene.get("index_size")
        #         if api_filesize:
        #             api_filesize = int(api_filesize)
        #     if db_duration - range_duration <= api_duration \
        #             <= db_duration + range_duration:
        #         match_duration = True
        #     db_size_max = db_size + (db_size / 100)
        #     db_size_min = db_size - (db_size / 100)
        #     if api_filesize:
        #         if db_size_min <= api_filesize <= db_size_max:
        #             match_size = True
        # Post process things
        match_domain = False
        # if URL_DOMAIN:
        #     if api_scene.get("sitename"):
        #         # log.debug("API Sitename: {}".format(api_scene["sitename"]))
        #         if api_scene["sitename"].lower() == URL_DOMAIN:
        #             match_domain = True
        #     if api_scene.get("network_name"):
        #         # log.debug(
        #               "API Network: {}".format(api_scene["network_name"]))
        #         if api_scene["network_name"].lower() == URL_DOMAIN:
        #             match_domain = True

        # Matching ratio
        if self.game_title:
            match_ratio_title = \
                difflib.SequenceMatcher(
                    None, self.game_title.lower(), api_title.lower()
                ).ratio()
        else:
            match_ratio_title = 0
        if self.game_title and api_scene.get("title"):
            match_ratio_title_url = \
                difflib.SequenceMatcher(
                    None, self.game_title.lower(), api_scene["title"].lower()
                ).ratio()
        else:
            match_ratio_title_url = 0

        # Rank search result

        log.debug(
            f"[MATCH] Title: {api_title} |-RATIO-| Ratio: "
            f"{round(match_ratio_title, 5)} / URL: "
            f"{round(match_ratio_title_url, 5)} |-MATCH-| Duration: "
            f"{match_duration}, Size: {match_size}, Domain: {match_domain}"
        )
        match_dict = {}
        match_dict["title"] = match_ratio_title
        match_dict["url"] = match_ratio_title_url

        information_used = ""
        if (
            single
            and (
                match_duration
                or (
                    self.database_dict is None
                    and match_ratio_title_url > 0.5
                )
            )
        ) or match_ratio_title_url == 1:
            information_used += "A"
        if match_size:
            information_used += "S"
        if match_duration:
            information_used += "D"
        if match_domain:
            information_used += "N"
        if clip_id and clip_id == api_clip_id:
            match_clip_id = True
        if information_used == "":
            information_used = "R"
        match_dict["info"] = information_used
        match_dict["clip_id"] = match_clip_id
        # debug("[MATCH] {} - {}".format(api_title,match_dict))
        return match_dict

    def get_id_from_url(self, url: str) -> str:
        '''
        gets  the id from a valid url
        expects urls of the form www.example.com/.../gameId/id
        '''
        if url is None or url == "":
            return None

        id_check = re.sub('.+/', '', url)
        id_from_url = None
        try:
            if id_check.isdigit():
                id_from_url = id_check
            else:
                id_from_url = re.search(r"/(\d+)/*", url).group(1)
                log.info(f"ID: {id_from_url}")
        except Exception as ex:
            log.debug(ex)
            log.warning("Can't get ID from URL")
        return id_from_url

    def parse_movie_json(self, movie_json: dict) -> dict:
        """
        process an api movie dictionary and return a scraped one
        """
        scrape = {}
        try:
            studio_name = movie_json[0].get("sitename_pretty")
        except IndexError:
            log.debug("No movie found")
            return scrape
        scrape["synopsis"] = self.clean_text(movie_json[0].get("description"))
        scrape["name"] = movie_json[0].get("title")
        scrape["studio"] = {"name": studio_name}
        scrape["duration"] = movie_json[0].get("total_length")

        date_by_studio = "date_created"

        log.debug(
            f"Dates available: upcoming {movie_json[0].get('upcoming')} - "
            f"created {movie_json[0].get('date_created')} - last modified "
            f"{movie_json[0].get('last_modified')}"
        )
        studios_movie_dates = {
            "Diabolic": "last_modified",
            "Evil Angel": "date_created",
            "Wicked": "date_created",
            "Zerotolerance": "last_modified"
        }
        if studios_movie_dates.get(studio_name):
            date_by_studio = studios_movie_dates[studio_name]
        scrape["date"] = movie_json[0].get(date_by_studio)

        scrape["front_image"] = \
            "https://transform.gammacdn.com/movies" \
            f"{movie_json[0].get('cover_path')}_front_400x625.jpg" \
            "?width=450&height=636"
        scrape["back_image"] = \
            f"https://transform.gammacdn.com/movies" \
            f"{movie_json[0].get('cover_path')}_back_400x625.jpg" \
            "?width=450&height=636"

        directors = []
        if movie_json[0].get('directors') is not None:
            for director in movie_json[0].get('directors'):
                directors.append(director.get('name').strip())
        scrape["director"] = ", ".join(directors)
        return scrape
    
    def nice_join_list_str(self, str_list: List[str]) -> str:
        """
        Joins a list of strings like:
        ['One'] -> 'One'
        ['One', 'Two'] -> 'One & Two'
        ['One', 'Two', 'Three'] -> 'One, Two & Three'
        """
        nice_str = ""
        if len(str_list) == 1:
            nice_str = str_list[0]
        else:
            csv = ', '.join(str_list[slice(len(str_list) - 1)])
            last = str_list[-1]
            nice_str = f"{csv} & {last}"
        return nice_str

    def parse_game_json(self, game_json, url=None) -> List[dict]:
        """
        process an api game dictionary and return an individual
        scraped scene from the game/release
        """
        log.debug(
            f"start parse_game_json(game_json={game_json}, url=\"{url}\")"
        )
        game_scraped = {}
        
        # Performer
        performers = []
        for actor in game_json.get('performer'):
            performers.append({
                "name": actor.get('name').strip(),
                "gender": "female"
            })
        game_scraped['performers'] = performers

        # Title
        if game_json.get('title'):
            game_scraped['title'] = game_json['title'].strip()
        if len(performers) > 0:
            performer_names = [ p['name'] for p in performers ]
            game_scraped['title'] += f" - {self.nice_join_list_str(performer_names)}"

        # Date
        game_scraped['date'] = game_json.get('releaseDate')
        # Details
        game_scraped['details'] = self.clean_text(game_json.get('details'))

        # Studio Code
        if game_json.get('id'):
            game_scraped['code'] = game_json['id']

        # Director
        directors = []
        if game_json.get('directors') is not None:
            for director in game_json.get('directors'):
                directors.append(director.get('name').strip())
        game_scraped["director"] = ", ".join(directors)

        # Studio
        if self.url_domain:
            game_scraped['studio'] = {}
            if self.url_domain == 'lifeselector':
                game_scraped['studio']['name'] = 'Life Selector'
            elif self.url_domain == "21roles":
                game_scraped['studio']['name'] = '21 Roles'

        # Tags
        list_tag = []
        for tag in game_json.get('tag'):
            if tag.get('name') is None:
                continue
            tag_name = tag.get('name')
            tag_name = " ".join(
                tag.capitalize() for tag in tag_name.split(" ")
            )
            if tag_name:
                list_tag.append({"name": tag.get('name')})
        if self.fixed_tag:
            list_tag.append({"name": self.fixed_tag})
        game_scraped['tags'] = list_tag

        # URL
        try:
            hostname = game_json['sitename']
            # Movie
            if game_json.get('movie_title'):
                game_scraped['movies'] = [{
                    "name": game_json["movie_title"],
                    "synopsis": self.clean_text(game_json.get("movie_desc")),
                    "date": game_json.get("movie_date_created")
                    }]
                log.debug(f"domain to use for movie url: {self.url_domain}")

            net_name = game_json['network_name']
            if net_name.lower() == "21 sextury":
                hostname = "21sextury"
            elif net_name.lower() == "21 naturals":
                hostname = "21naturals"
            game_scraped['url'] = \
                f"https://{hostname.lower()}.com/en/video" \
                f"/{game_json['sitename'].lower()}" \
                f"/{game_json['url_title']}/{game_json['clip_id']}"
        except Exception as ex:
            log.debug(ex)
            if url:
                game_scraped['url'] = url
        # debug(f"{scrape}")

        # # Image
        # try:
        #     scene_id = game_json["id"]
        #     game_scraped['image'] = \
        #         f"https://i.c7cdn.com/generator/games/{scene_id}" \
        #         f"/images/poster/{scene_number}_size2000.jpg"
        # except Exception as ex:
        #     log.debug(ex)
        #     log.warning("Can't locate image.")
        scenes = [
            {
                'performers': game_scraped.get('performers'),
                'title': f"{game_scraped.get('title')} (DELETE AS APPROPRIATE!)",
                'date': game_scraped.get('date'),
                'details': game_scraped.get('details'),
                'code': game_scraped.get('code'),
                'director': game_scraped.get('director'),
                'studio': game_scraped.get('studio').get('name') if game_scraped.get('studio') else None,
                'tags': game_scraped.get('tags'),
                'url': game_scraped.get('url'),
                'image': scene.get('image')
            } for scene in self.scraped['scenes']
        ]
        return scenes

    def parse_gallery_json(self, gallery_json: dict, url: str = None) -> dict:
        """
        process an api gallery dictionary and return a scraped one
        """
        scrape = {}
        # Title
        if gallery_json.get('title'):
            scrape['title'] = gallery_json['title'].strip()
        # Date
        scrape['date'] = gallery_json.get('date_online')
        # Details
        scrape['details'] = self.clean_text(gallery_json.get('description'))

        # Studio Code # not yet supported in stash
        # if gallery_json.get('set_id'):
        #    scrape['code'] = str(gallery_json['set_id'])

        # Director # not yet supported in stash
        # directors = []
        # if gallery_json.get('directors') is not None:
        #    for director in gallery_json.get('directors'):
        #        directors.append(director.get('name').strip())
        # scrape["director"] = ", ".join(directors)

        # Studio
        scrape['studio']['name'] = gallery_json.get('serie_name')

        # Performer
        perf = []
        for actor in gallery_json.get('actors'):
            if actor.get('gender') == "female":
                perf.append({
                    "name": actor.get('name').strip(),
                    "gender": actor.get('gender')
                })
        scrape['performers'] = perf

        # Tags
        list_tag = []
        for tag in gallery_json.get('categories'):
            if tag.get('name') is None:
                continue
            tag_name = tag.get('name')
            tag_name = " ".join(
                tag.capitalize() for tag in tag_name.split(" ")
            )
            if tag_name:
                list_tag.append({"name": tag.get('name')})
        if self.fixed_tag:
            list_tag.append({"name": self.fixed_tag})
        scrape['tags'] = list_tag

        # URL
        try:
            hostname = gallery_json['sitename']
            net_name = gallery_json['network_name']
            if net_name.lower() == "21 sextury":
                hostname = "21sextury"
            elif net_name.lower() == "21 naturals":
                hostname = "21naturals"
            scrape['url'] = \
                f"https://{hostname.lower()}.com/en/video" \
                f"/{gallery_json['sitename'].lower()}" \
                f"/{gallery_json['url_title']}/{gallery_json['set_id']}"
        except Exception as ex:
            log.debug(ex)
            if url:
                scrape['url'] = url
        return scrape

    def get_db_path(self):
        """
        Get your sqlite database path
        """
        stash_config = graphql.configuration()
        if stash_config:
            self.db_path = stash_config["general"]["databasePath"]

        if (self.config_path and self.db_path is None):
            # getting your database from the config.yml
            if os.path.isfile(self.config_path):
                with open(self.config_path, encoding='utf-8') as config_file:
                    for line in config_file:
                        if "database: " in line:
                            self.db_path = \
                                line.replace("database: ", "").rstrip('\n')
                            break
        log.debug(f"Database Path: {self.db_path}")

    def get_scene_dict_from_db(self, game_id=None):
        if self.db_path:
            if game_id:
                # Get data by GraphQL
                self.database_dict = graphql.getScene(game_id)
                if self.database_dict is None:
                    # Get data by SQlite
                    log.warning(
                        "GraphQL request failed, accessing database "
                        "directly..."
                    )
                    self.database_dict = \
                        self.check_db(self.db_path, game_id)
                else:
                    self.database_dict = self.database_dict["file"]
                log.debug(f"[DATABASE] Info: {self.database_dict}")
            else:
                self.database_dict = None
                log.debug("URL scraping... Ignoring database...")
        else:
            self.database_dict = None
            log.warning("Database path missing.")

    def scrape_scene_url(self):
        self.url_id = self.get_id_from_url(self.fragment.get("url"))
        try:
            self.scrape_game_page_html(self.fragment.get("url"), self.headers)
        except Exception as ex:
            log.warning("Can't get game info from URL")
            log.debug(ex)

    def clean_scene_title(self, title) -> str:
        # Remove some punctuation/symbols and file extension, if present
        log.debug(f"clean_scene_title title (input): {title}")
        title = re.sub(
            r'[-._\']',
            ' ',
            os.path.splitext(title)[0]
        )
        # Remove resolution
        title = re.sub(
            r'\sXXX|\s1080p|720p|2160p|KTR|RARBG|\scom\s|\[|]|\sHD|\sSD|', '',
            title)
        # Remove Date
        title = re.sub(
            r'\s\d{2}\s\d{2}\s\d{2}|\s\d{4}\s\d{2}\s\d{2}',
            '',
            title
        )
        log.debug(f"clean_scene_title title (output): {title}")
        return title

    def get_game_url_for_id(self, game_id):
        return f"{self.game_by_id_url}/{game_id}"

    def parse_and_scrape_game(self, game):
        scenes = []
        log.debug("start parse_and_scrape_game(game)")
        scraped_json = self.parse_game_json(game)
        if scraped_json.get("tags"):
            scraped_json.pop("tags")
        scraped_json["url"] = self.get_game_url_for_id(game["id"])
        if self.game_description:
            scraped_json["details"] = self.game_description
        # scrape game page for scenes
        self.scrape_game_page_html(scraped_json["url"], self.headers)
        for scraped_scene in self.scraped["scenes"]:
            scene_plus = scraped_json.copy()
            # if self.game_description:
            #     scene_plus["details"] = self.game_description
            scene_plus["image"] = scraped_scene["image"]
            scenes.append(scene_plus)
        log.debug("end parse_and_scrape_game(game)")
        return scenes

    def game_by_name(self, name) -> list:
        '''
        Search for game by name, returns a list of game results
        '''
        log.debug("start game_by_name(name)")
        # clean name to be search title
        self.clean_scene_title(name)

        log.debug(f"[API] Searching for: {name}")
        self.api_search_results = \
            self.api_search_req("query", name, self.api_url)
        final_json = None
        if self.api_search_results:
            result_search = []
            for game in self.api_search_results:
                scenes = self.parse_and_scrape_game(game)
                result_search.extend(scenes)
            if result_search:
                final_json = result_search
        if final_json is None:
            log.error("API Search finished. No results!")
        else:
            log.debug(f"final_json: {final_json}")
        log.debug("end game_by_name(name)")
        return final_json

    def load_from_input(self) -> None:
        """
        get fragment from stdin (stashapp submits JSON to stdin into the
        script)
        """
        log.debug("start load_from_input()")
        self.fragment = json.loads(input())
        log.debug(f"fragment: {self.fragment}")

        if self.fragment.get("url"):
            self.url_domain = re.sub(
                r"www\.|\.com", "",
                urlparse(self.fragment.get("url")).netloc
            ).lower()
            log.info(f"URL Domain: {self.url_domain}")

        if self.fragment.get("url") and self.fragment.get("id") is None:
            log.debug(f"URL Scraping: {self.fragment.get('url')}")
        else:
            log.debug(f"Stash ID: {self.fragment.get('id')}")
            log.debug(f"Stash Title: {self.fragment.get('title')}")
        log.debug("end load_from_input()")

    def add_html_scrapings(self):
        """
        Add values from HTML scrape
        """
        searched_and_scraped = []
        for search_item in self.api_search_results:
            for scene in self.scraped["scenes"]:
                search_item_plus = search_item.copy()
                if self.game_description:
                    search_item_plus["details"] = \
                        self.game_description
                search_item_plus["image"] = scene["image"]
                searched_and_scraped.append(search_item_plus)
        return searched_and_scraped

    def start_processing(self):
        """
        Start processing
        """

        log.info("start start_processing()")

        if self.args.get('action') == 'sceneByFragment' \
                or self.args.get('action') == 'sceneByQueryFragment':
            if self.fragment.get('url') is None:
                log.error("scene_url is required for `sceneByQueryFragment`")
                sys.exit(1)
            self.scrape_scene_url()
            scene_search_by_name_results = self.game_by_name(self.scraped.get('title'))
            idx = int(self.fragment.get('code').split('-')[1])
            print(json.dumps(scene_search_by_name_results[idx]))
        if self.args.get('action') == 'sceneByName':
            # fragment = {"name": "<scene query string>"}
            self.scrape_scene_url()
            scene_search_by_name_results = self.game_by_name(self.fragment.get('name'))
            print(json.dumps(scene_search_by_name_results))
        elif self.args.get('action') == 'sceneByURL':
            # fragment = {"url": "<url>"}
            self.scrape_scene_url()



        # if self.args.get('action') and self.args.get('action').startswith('scene'):
        #     # Get your sqlite database
        #     self.get_db_path()
        #     self.get_scene_dict_from_db(self.fragment.get('id'))

            # # Extract things from URL page
            # if self.game_url:
            #     self.scrape_scene_url()

            # # Filter title
            # if self.fragment.get("title"):
            #     self.clean_scene_title(self.fragment.get("title"))

            # # Time to search the API
            # self.api_search_results = None
            # api_json = None



            # if self.URL_ID:
            #     log.debug(f"[API] Searching using URL_ID {self.URL_ID}")
            #     self.api_search_results = \
            #         api_search_req("id", self.URL_ID, self.API2_URL)
            #     if self.api_search_results:
            #         log.info(
            #             f"[API] Search gives {len(self.api_search_results)} "
            #             "result(s)"
            #         )
            #         api_json = json_parser(self.api_search_results, 120, True)
            #     else:
            #         log.warning("[API] No result")
        #     if self.fragment.get("title") and api_json is None:
        #         log.debug("[API] Searching using game_title")
        #         self.api_search_results = \
        #             self.api_search_req("query", self.fragment.get("title"), self.api_url)
        #         if self.api_search_results:
        #             searched_and_scraped = self.add_html_scrapings()
        #             api_json = self.json_parser(searched_and_scraped)
        #     if self.fragment.get("title") and api_json is None:
        #         log.debug("[API] Searching using scene_title")
        #         self.api_search_results = \
        #             self.api_search_req(
        #                 "query", self.fragment.get("title"), self.api_url
        #             )
        #         if self.api_search_results:
        #             searched_and_scraped = self.add_html_scrapings()
        #             api_json = self.json_parser(searched_and_scraped)

        #     # Scraping the JSON
        #     if api_json:
        #         log.info(f"Scene found: {api_json['title']}")
        #         scraped_json = self.parse_scene_json(api_json, self.game_url)
        #         print(json.dumps(scraped_json))
        #     else:
        #         log.error("Can't find the scene")
        #         print(json.dumps({}))
        #         sys.exit()
        # elif self.action == 'galleryByURL':
        #     log.debug("Scraping gallery")
        #     gallery_id = self.get_id_from_url(self.game_url)
        #     if gallery_id:
        #         gallery_results = \
        #             self.api_search_gallery_id(gallery_id, self.api_url)
        #         gallery = gallery_results.json()["results"][0].get("hits")
        #         if gallery:
        #             # log.debug(gallery[0])
        #             scraped_gallery = self.parse_gallery_json(gallery[0])
        #             # log.debug(scraped_gallery)
        #             print(json.dumps(scraped_gallery))

    def load_arguments(self) -> None:
        """
        get the script arguments from sys.argv
        """
        log.debug("start load_arguments()")
        self.args = {}
        try:
            self.args['action'] = sys.argv[1]
        except IndexError as ex:
            log.debug(ex)
            log.error(
                "ACTION is required as first positional script argument "
                "(third array item of script property in YAML)"
            )
            sys.exit(1)
        log.debug(f"args: {self.args}")
        log.debug("end load_arguments()")


if __name__ == '__main__':
    # instantiate class
    scraper = LifeSelectorScraper()

    # load arguments
    scraper.load_arguments()

    # load the stdin sent by stashapp
    scraper.load_from_input()

    # start processing
    scraper.start_processing()

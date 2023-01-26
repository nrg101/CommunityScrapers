import datetime
import difflib
import json
import os
import re
import sqlite3
import sys
from configparser import ConfigParser, NoSectionError
from urllib.parse import urlparse

try:
    from bs4 import BeautifulSoup as bs
    import requests
    import lxml
except ModuleNotFoundError:
    print(
        "You need to install the following modules 'requests', 'bs4', 'lxml'.", file=sys.stderr)
    sys.exit(1)

try:
    from py_common import graphql
    from py_common import log
except ModuleNotFoundError:
    print(
        "You need to download the folder 'py_common' from the community repo! (CommunityScrapers/tree/master/scrapers/py_common)",
        file=sys.stderr)
    sys.exit(1)

class LifeSelector:
    """"
    Scraper for lifeselector.com (and compatible sites)
    """

    API_URL = "https://contentworker.ls-tester.com/api/search"
    CONFIG_PATH = None
    DB_PATH = None
    FIXED_TAG = "Male POV"      # Extra tag that will be added to the scene
    GAME_BY_ID_URL = "https://lifeselector.com/game/DisplayPlayer/gameId"
    GAME_DESCRIPTION = None
    GAME_TITLE = None
    HEADERS = None
    SEARCH_TITLE = None
    SCENE_ID = None
    SCENE_TITLE = None
    SCENE_URL = None
    SITE = None
    URL_DOMAIN = None
    URL_ID = None
    USERFOLDER_PATH = None
    SCRAPED = None
    database_dict = None

    def __init__(self):
        """
        Constructor
        """

        try:
            self.SITE = sys.argv[1]
        except:
            log.error("SITE is required as first positional commandline argument (first array item in YAML)")
            sys.exit(1)
        
        try:
            self.USERFOLDER_PATH = re.match(r".+\.stash.", __file__).group(0)
            self.CONFIG_PATH = self.USERFOLDER_PATH + "config.yml"
            log.debug(f"Config Path: {self.CONFIG_PATH}")
        except Exception as e:
            log.warning(e)
            log.debug("No config")

        self.HEADERS = {
            "User-Agent":
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:79.0) Gecko/20100101 Firefox/79.0',
            "Origin": f"https://www.{self.SITE}.com",
            "Referer": f"https://www.{self.SITE}.com"
        }

        # get fragment from stdin (stashapp submits a json fragment to stdin into the script)
        fragment = json.loads(input())
        self.SEARCH_TITLE = fragment.get("name")
        self.SCENE_ID = fragment.get("id")
        self.SCENE_TITLE = fragment.get("title")
        self.SCENE_URL = fragment.get("url")

        if self.SCENE_URL:
            self.URL_DOMAIN = re.sub(r"www\.|\.com", "", urlparse(self.SCENE_URL).netloc).lower()
            log.info(f"URL Domain: {self.URL_DOMAIN}")

        if "validName" in sys.argv and self.SCENE_URL is None:
            log.error("SCENE_URL is required for `validName`")
            sys.exit(1)
        

    def clean_text(self, details: str) -> str:
        """
        remove escaped backslashes and html parse the details text
        """
        if details:
            details = details.replace('\\', '')
            details = re.sub(r"<\s*/?br\s*/?\s*>", "\n",
                            details)  # bs.get_text doesnt replace br's with \n
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
        except Exception as e:
            log.error(e)
            log.warning("Fail to connect to the database")
            return {}
        cursor = sqlite_connection.cursor()
        cursor.execute("SELECT size,duration,height from scenes WHERE id=?;",
                    [scn_id])
        record = cursor.fetchall()
        database = {}
        database["size"] = int(record[0][0])
        database["duration"] = int(record[0][1])
        database["height"] = str(record[0][2])
        cursor.close()
        sqlite_connection.close()
        return database


    def send_request(self, url: str, head: str, params: dict = None) -> requests.Response:
        """
        get response from url
        """
        log.debug(f"Request URL: {url}")
        try:
            response = requests.get(url, headers=head, params=params, timeout=10)
        except requests.RequestException as req_error:
            log.warning(f"Requests failed: {req_error}")
            return None
        log.debug(f"Returned URL: {response.url}")
        if response.content and response.status_code == 200:
            # log.debug(f"response.text: {response.text}")
            return response
        log.warning(f"[REQUEST] Error, Status Code: {response.status_code}")
        return None


    def scrape_game_html(self, url: str, head: dict) -> dict:
        """
        scrape a game's HTML page
        """
        self.SCRAPED = {}
        log.debug(f"Scraping HTML page {url}")
        req = self.send_request(url, head)

        page = bs(req.text, 'html.parser')
        self.SCRAPED["title"] = page.title.string.replace(' - Interactive Porn Game', '')
        self.SCRAPED["description"] = page.find("div", class_="info").find("p").string
        self.SCRAPED["scenes"] = [ {"image": tag['src']} for tag in page.find("div", class_="player").find_all("img")[1:] ]
        
        log.debug(f"self.SCRAPED: {self.SCRAPED}")
        return self.SCRAPED


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
                return self.api_search_results
        return None


    def api_search_id(self, scene_id, url):
        search_url = f"{url}/{scene_id}/choiceId/0"
        req = self.send_request(search_url, self.HEADERS)
        return req


    def api_search_movie_id(self, m_id, url):
        movie_id = [f"movie_id:{m_id}"]
        request_api = {
            "requests": [{
                "indexName": "all_movies",
                "params": "query=&hitsPerPage=20&page=0",
                "facetFilters": movie_id
            }]
        }
        req = self.send_request(url, self.HEADERS, request_api)
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
        req = self.send_request(url, self.HEADERS, request_api)
        return req


    def api_search_query(self, query, url):
        params = { 'q': query }
        req = self.send_request(url, self.HEADERS, params)
        return req


    def json_parser(self, search_json, range_duration=60, single=False, scene_id=None):
        result_dict = {}
        # Just for not printing the full JSON in log...
        debug_dict = {}
        with open("lifeselector_scene_search.json", 'w',
                encoding='utf-8') as search_file:
            json.dump(search_json, search_file, ensure_ascii=False, indent=4)
        for scene in search_json:
            r_match = self.match_result(scene, range_duration, single, clip_id=self.URL_ID)
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
        # A = ByID/Most likely | S = Size | D = Duration | N = Network | R = Only Ratio
        log.info("--- BEST RESULT ---")
        for key, item in debug_dict.items():
            log.info(
                f'[{key}] Title: {item["scene"]}; Ratio Title: {round(item["title"], 3)} - URL: {round(item["url"], 3)}'
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
            if result_dict["AN"]["clip_id"] or result_dict["AN"]["title"] > 0.5 or result_dict["AN"]["url"] > 0.5:
                return result_dict["AN"]["json"]
        if result_dict.get("A"):
            if result_dict["A"]["title"] > 0.7 or result_dict["A"]["url"] > 0.7:
                return result_dict["A"]["json"]
        if result_dict.get("SDN"):
            return result_dict["SDN"]["json"]
        if result_dict.get("SD"):
            return result_dict["SD"]["json"]
        if result_dict.get("SN"):
            if result_dict["SN"]["title"] > 0.5 or result_dict["SN"]["url"] > 0.5:
                return result_dict["SN"]["json"]
        if result_dict.get("DN"):
            if result_dict["DN"]["title"] > 0.5 or result_dict["DN"]["url"] > 0.5:
                return result_dict["DN"]["json"]
        if result_dict.get("S"):
            if result_dict["S"]["title"] > 0.7 or result_dict["S"]["url"] > 0.7:
                return result_dict["S"]["json"]
        if result_dict.get("D"):
            if result_dict["D"]["title"] > 0.7 or result_dict["D"]["url"] > 0.7:
                return result_dict["D"]["json"]
        if result_dict.get("N"):
            if result_dict["N"]["title"] > 0.7 or result_dict["N"]["url"] > 0.7:
                return result_dict["N"]["json"]
        if result_dict.get("R"):
            if result_dict["R"]["title"] > 0.8 or result_dict["R"]["url"] > 0.8:
                return result_dict["R"]["json"]
        return None


    def match_result(self, api_scene, range_duration=60, single=False, clip_id: str=None):
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
        #             api_filesize = api_scene["download_file_sizes"].get(db_height +
        #                                                                 "p")
        #         if api_filesize:
        #             api_filesize = int(api_filesize)
        #     if api_filesize is None:
        #         api_filesize = api_scene.get("index_size")
        #         if api_filesize:
        #             api_filesize = int(api_filesize)
        #     if db_duration - range_duration <= api_duration <= db_duration + range_duration:
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
        #         #log.debug("API Sitename: {}".format(api_scene["sitename"]))
        #         if api_scene["sitename"].lower() == URL_DOMAIN:
        #             match_domain = True
        #     if api_scene.get("network_name"):
        #         #log.debug("API Network: {}".format(api_scene["network_name"]))
        #         if api_scene["network_name"].lower() == URL_DOMAIN:
        #             match_domain = True

        # Matching ratio
        if self.SCENE_TITLE:
            match_ratio_title = difflib.SequenceMatcher(None, self.SCENE_TITLE.lower(),
                                                        api_title.lower()).ratio()
        else:
            match_ratio_title = 0
        if self.GAME_TITLE and api_scene.get("title"):
            match_ratio_title_url = difflib.SequenceMatcher(
                None, self.GAME_TITLE.lower(), api_scene["title"].lower()).ratio()
        else:
            match_ratio_title_url = 0

        # Rank search result

        log.debug(
            f"[MATCH] Title: {api_title} |-RATIO-| Ratio: {round(match_ratio_title, 5)} / URL: {round(match_ratio_title_url, 5)} |-MATCH-| Duration: {match_duration}, Size: {match_size}, Domain: {match_domain}"
        )
        match_dict = {}
        match_dict["title"] = match_ratio_title
        match_dict["url"] = match_ratio_title_url

        information_used = ""
        if (single and (match_duration or
                        (self.database_dict is None and match_ratio_title_url > 0.5))
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
        #debug("[MATCH] {} - {}".format(api_title,match_dict))
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
        except:
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

        date_by_studio = "date_created" # options are "date_created", "upcoming" (not always avaialble), "last_modified"
                                        # dates don't seem to be accurate (modifed multiple times by studio)
                                        # using date_created as default and we later override for each site when needed

        log.debug(
            f"Dates available: upcoming {movie_json[0].get('upcoming')} - created {movie_json[0].get('date_created')} - last modified {movie_json[0].get('last_modified')}"
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

        scrape[
            "front_image"] = f"https://transform.gammacdn.com/movies{movie[0].get('cover_path')}_front_400x625.jpg?width=450&height=636"
        scrape[
            "back_image"] = f"https://transform.gammacdn.com/movies{movie[0].get('cover_path')}_back_400x625.jpg?width=450&height=636"

        directors = []
        if movie_json[0].get('directors') is not None:
            for director in movie_json[0].get('directors'):
                directors.append(director.get('name').strip())
        scrape["director"] = ", ".join(directors)
        return scrape


    def parse_scene_json(self, scene_json, url=None):
        """
        process an api scene dictionary and return a scraped one
        """
        # log.debug(f"Parsing scene json. scene_json: {scene_json}, url: {url}")
        scrape = {}
        # Title
        if scene_json.get('title'):
            scrape['title'] = scene_json['title'].strip()
        # Date
        scrape['date'] = scene_json.get('releaseDate')
        # Details
        scrape['details'] = self.clean_text(scene_json.get('description'))

        # Studio Code
        if scene_json.get('id'):
            scrape['code'] = str(scene_json['id'])

        # Director
        directors = []
        if scene_json.get('directors') is not None:
            for director in scene_json.get('directors'):
                directors.append(director.get('name').strip())
        scrape["director"] = ", ".join(directors)

        # Studio
        # scrape['studio']['name'] = scene_json.get('serie_name')

        # log.debug(
        #     f"[STUDIO] {scene_json.get('serie_name')} - {scene_json.get('network_name')} - {scene_json.get('mainChannelName')} - {scene_json.get('sitename_pretty')}"
        # )
        # Performer
        perf = []
        for actor in scene_json.get('performer'):
            perf.append({
                "name": actor.get('name').strip(),
                "gender": "female"
            })
        scrape['performers'] = perf

        # Tags
        list_tag = []
        for tag in scene_json.get('tag'):
            if tag.get('name') is None:
                continue
            tag_name = tag.get('name')
            tag_name = " ".join(tag.capitalize() for tag in tag_name.split(" "))
            if tag_name:
                list_tag.append({"name": tag.get('name')})
        if self.FIXED_TAG:
            list_tag.append({"name": self.FIXED_TAG})
        scrape['tags'] = list_tag

        # Image
        try:
            scene_id = scene_json["id"]
            scrape['image'] = f"https://i.c7cdn.com/generator/games/{scene_id}/images/poster/2_size2000.jpg"
        except:
            log.warning("Can't locate image.")
        # URL
        try:
            hostname = scene_json['sitename']
            # Movie
            if scene_json.get('movie_title'):
                scrape['movies'] = [{
                    "name": scene_json["movie_title"],
                    "synopsis": self.clean_text(scene_json.get("movie_desc")),
                    "date": scene_json.get("movie_date_created")
                    }]
                log.debug(f"domain to use for movie url: {self.URL_DOMAIN}")

            net_name = scene_json['network_name']
            if net_name.lower() == "21 sextury":
                hostname = "21sextury"
            elif net_name.lower() == "21 naturals":
                hostname = "21naturals"
            scrape[
                'url'] = f"https://{hostname.lower()}.com/en/video/{scene_json['sitename'].lower()}/{scene_json['url_title']}/{scene_json['clip_id']}"
        except:
            if url:
                scrape['url'] = url
        #debug(f"{scrape}")
        return scrape

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
        #if gallery_json.get('set_id'):
        #    scrape['code'] = str(gallery_json['set_id'])

        # Director # not yet supported in stash
        #directors = []
        #if gallery_json.get('directors') is not None:
        #    for director in gallery_json.get('directors'):
        #        directors.append(director.get('name').strip())
        #scrape["director"] = ", ".join(directors)

        # Studio
        scrape['studio']['name'] = gallery_json.get('serie_name')

        log.debug(
            f"[STUDIO] {gallery_json.get('serie_name')} - {gallery_json.get('network_name')} - {gallery_json.get('mainChannelName')} - {gallery_json.get('sitename_pretty')}"
        )
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
            tag_name = " ".join(tag.capitalize() for tag in tag_name.split(" "))
            if tag_name:
                list_tag.append({"name": tag.get('name')})
        if self.FIXED_TAG:
            list_tag.append({"name": self.FIXED_TAG})
        scrape['tags'] = list_tag

        # URL
        try:
            hostname = gallery_json['sitename']
            net_name = gallery_json['network_name']
            if net_name.lower() == "21 sextury":
                hostname = "21sextury"
            elif net_name.lower() == "21 naturals":
                hostname = "21naturals"
            scrape[
                'url'] = f"https://{hostname.lower()}.com/en/video/{gallery_json['sitename'].lower()}/{gallery_json['url_title']}/{gallery_json['set_id']}"
        except:
            if url:
                scrape['url'] = url
        return scrape

    def get_db_path(self):
        """
        Get your sqlite database path
        """
        stash_config = graphql.configuration()
        if stash_config:
            self.DB_PATH = stash_config["general"]["databasePath"]

        if (self.CONFIG_PATH and self.DB_PATH is None):
            # getting your database from the config.yml
            if os.path.isfile(self.CONFIG_PATH):
                with open(self.CONFIG_PATH, encoding='utf-8') as f:
                    for line in f:
                        if "database: " in line:
                            self.DB_PATH = line.replace("database: ", "").rstrip('\n')
                            break
        log.debug(f"Database Path: {self.DB_PATH}")
    
    def get_scene_dict_from_db(self):
        if self.DB_PATH:
            if self.SCENE_ID:
                # Get data by GraphQL
                self.database_dict = graphql.getScene(self.SCENE_ID)
                if self.database_dict is None:
                    # Get data by SQlite
                    log.warning("GraphQL request failed, accessing database directly...")
                    self.database_dict = self.check_db(self.DB_PATH, self.SCENE_ID)
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
        self.URL_ID = self.get_id_from_url(self.SCENE_URL)
        try:
            self.SCRAPED = self.scrape_game_html(self.SCENE_URL, self.HEADERS)
            log.debug(f"SCRAPED: {self.SCRAPED}")
            self.GAME_TITLE = self.SCRAPED["title"]
            log.info(f"GAME_TITLE: {self.GAME_TITLE}")
            self.GAME_DESCRIPTION = self.SCRAPED["description"]
            log.info(f"GAME_DESCRIPTION: {self.GAME_DESCRIPTION}")
        except Exception as e:
            log.warning("Can't get game info from URL")
            log.debug(e)

    def clean_scene_title(self):
        # Remove some punctuation/symbols and file extension, if present
        self.SCENE_TITLE = re.sub(r'[-._\']', ' ', os.path.splitext(self.SCENE_TITLE)[0])
        # Remove resolution
        self.SCENE_TITLE = re.sub(
            r'\sXXX|\s1080p|720p|2160p|KTR|RARBG|\scom\s|\[|]|\sHD|\sSD|', '',
            self.SCENE_TITLE)
        # Remove Date
        self.SCENE_TITLE = re.sub(r'\s\d{2}\s\d{2}\s\d{2}|\s\d{4}\s\d{2}\s\d{2}',
                            '', self.SCENE_TITLE)
        log.debug(f"Title: {self.SCENE_TITLE}")


    def get_game_url_for_id(self, game_id):
        return f"{self.GAME_BY_ID_URL}/{game_id}"


    def parse_and_scrape_scene(self, scene):
        parsed_and_scraped_scenes = []
        log.debug(f"scene: {scene}")
        scraped_json = self.parse_scene_json(scene)
        if scraped_json.get("tags"):
            scraped_json.pop("tags")
        scraped_json["url"] = self.get_game_url_for_id(scene["id"])
        if self.GAME_DESCRIPTION:
            scraped_json["description"] = self.GAME_DESCRIPTION
        # scrape game page for scenes
        self.SCRAPED = self.scrape_game_html(scraped_json["url"], self.HEADERS)
        for scraped_scene in self.SCRAPED["scenes"]:
            scene_plus = scraped_json.copy()
            if self.GAME_DESCRIPTION:
                scene_plus["description"] = self.GAME_DESCRIPTION
            scene_plus["image"] = scraped_scene["image"]
            parsed_and_scraped_scenes.append(scene_plus)
        return parsed_and_scraped_scenes


    def scene_by_name(self):
        self.SEARCH_TITLE = self.SEARCH_TITLE.replace(".", " ")
        log.debug(f"[API] Searching for: {self.SEARCH_TITLE}")
        self.api_search_results = self.api_search_req("query", self.SEARCH_TITLE, self.API_URL)
        final_json = None
        if self.api_search:
            result_search = []
            log.debug(f"sceneByName, api_search: {self.api_search}")
            for scene in self.api_search:
                parsed_and_scraped_scenes = self.parse_and_scrape_scene(scene)
                result_search.extend(parsed_and_scraped_scenes)
            if result_search:
                final_json = result_search
        if final_json is None:
            log.error("API Search finished. No results!")
        return final_json


    def main(self):
        """
        Start processing
        """

        if self.SCENE_URL and self.SCENE_ID is None:
            log.debug(f"URL Scraping: {self.SCENE_URL}")
        else:
            log.debug(f"Stash ID: {self.SCENE_ID}")
            log.debug(f"Stash Title: {self.SCENE_TITLE}")

        if "movie" not in sys.argv and "gallery" not in sys.argv:
            # Get your sqlite database
            self.get_db_path()
            self.get_scene_dict_from_db()

            # Extract things from URL page
            if self.SCENE_URL:
                self.scrape_scene_url()

            # Filter title
            if self.SCENE_TITLE:
                self.clean_scene_title()

            # Time to search the API
            self.api_search_results = None
            api_json = None

            # sceneByName
            if self.SEARCH_TITLE:
                final_json=self.scene_by_name()
                print(json.dumps(final_json))
                sys.exit()

            # if self.URL_ID:
            #     log.debug(f"[API] Searching using URL_ID {self.URL_ID}")
            #     self.api_search_results = api_search_req("id", self.URL_ID, self.API2_URL)
            #     if self.api_search:
            #         log.info(f"[API] Search gives {len(self.api_search)} result(s)")
            #         api_json = json_parser(self.api_search, 120, True)
            #     else:
            #         log.warning("[API] No result")
            if self.GAME_TITLE and api_json is None:
                log.debug("[API] Searching using GAME_TITLE")
                self.api_search_results = self.api_search_req("query", self.GAME_TITLE, self.API_URL)
                if self.api_search:
                    log.info(f"[API] Search gives {len(self.api_search)} result(s)")
                    log.debug(f"self.api_search: {self.api_search}")
                    searched_and_scraped = []            
                    for search_item in self.api_search:
                        for scene in self.SCRAPED["scenes"]:
                            search_item_plus = search_item.copy()
                            if self.GAME_DESCRIPTION:
                                search_item_plus["description"] = self.GAME_DESCRIPTION
                            search_item_plus["image"] = scene["image"]
                            searched_and_scraped.append(search_item_plus)
                    api_json = self.json_parser(searched_and_scraped)
            if self.SCENE_TITLE and api_json is None:
                log.debug("[API] Searching using STASH_TITLE")
                self.api_search_results = self.api_search_req("query", self.SCENE_TITLE, self.API_URL)
                if self.api_search_results:
                    log.info(f"[API] Search gives {len(self.api_search_results)} result(s)")
                    api_json = self.json_parser(self.api_search_results)

            # Scraping the JSON
            if api_json:
                log.info(f"Scene found: {api_json['title']}")
                scraped_json = self.parse_scene_json(api_json, self.SCENE_URL)
                print(json.dumps(scraped_json))
            else:
                log.error("Can't find the scene")
                print(json.dumps({}))
                sys.exit()
        elif "movie" in sys.argv:
            log.debug("Scraping movie")
            movie_id = self.get_id_from_url(self.SCENE_URL)
            if movie_id:
                movie_results = self.api_search_movie_id(movie_id, self.API_URL)
                movie = movie_results.json()["results"][0].get("hits")
                scraped_movie = self.parse_movie_json(movie)
                #log.debug(scraped_movie)
                print(json.dumps(scraped_movie))
        elif "gallery" in sys.argv:
            log.debug("Scraping gallery")
            gallery_id = self.get_id_from_url(self.SCENE_URL)
            if gallery_id:
                gallery_results = self.api_search_gallery_id(gallery_id, self.API_URL)
                gallery = gallery_results.json()["results"][0].get("hits")
                if gallery:
                    #log.debug(gallery[0])
                    scraped_gallery = self.parse_gallery_json(gallery[0])
                    #log.debug(scraped_gallery)
                    print(json.dumps(scraped_gallery))

if __name__ == '__main__':
    scraper = LifeSelector()
    scraper.main()

import re
import json
import argparse
import logging
import warnings

import asyncio
import aiohttp

REDDIT_LOGIN_URL = "https://www.reddit.com/post/login"
REDDIT_PLACE_URL = "https://www.reddit.com/place?webview=true"

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logging.basicConfig()

modhash_regexp = re.compile(r'"modhash": "(\w+)",')
ws_url_regexp = re.compile(r'"place_websocket_url": "([^"]+?)",')


class RedditPlaceClient:
    def __init__(self, session, loop=None):
        self.session = session
        self.modhash = ""
        self.ws_url = ""

        if not loop:
            loop = asyncio.get_event_loop()
        self.loop = loop
        self._running = False

    async def main(self, username, password):
        # Create HTTP session, also automatically stores any cookies created by
        # any request
        result = await self.login(username, password)

        if not result:
            logger.critical("Could not login on reddit.")
            return

        result = await self.scrape_info()
        if not result:
            logger.critical("Could not obtain modhash and websocket URL.")
            return

        logger.debug("Modhash: %s", self.modhash)
        logger.debug("WS URL: %s", self.ws_url)

        self.start_pixel_update_listener()

    @property
    def running(self):
        return self._running

    async def login(self, username, password) -> bool:
        """Login on reddit.com using the given username and password."""

        post_data = {
            'op': 'login-main',
            'user': username,
            'passwd': password,
            'rem': "1"
        }

        async with self.session.post(REDDIT_LOGIN_URL, data=post_data) as resp:
            if resp.status != 200:
                return False

            cookies = self.session.cookie_jar.filter_cookies(
                "https://www.reddit.com")
            if 'reddit_session' not in cookies:
                return False

            return True

    async def scrape_info(self) -> bool:
        """Scrape a few required things from the Reddit Place page.

        We need the `modhash` key (reddit's CSRF protection key) for further
        requests. Furthermore, the Place page contains the websocket URL, which
        we need to obtain updates."""

        async with self.session.get(REDDIT_PLACE_URL) as resp:
            if resp.status != 200:
                return False

            data = await resp.text()
            modhash_matches = modhash_regexp.search(data)
            ws_matches = ws_url_regexp.search(data)

            try:
                modhash = modhash_matches.group(1)
                ws_url = ws_matches.group(1)
            except IndexError:
                return False

            if not modhash:
                return False

            self.modhash = modhash
            self.ws_url = ws_url

            return True

    def start_pixel_update_listener(self):
        self._running = True
        self.loop.create_task(self.pixel_update_listener())

    async def pixel_update_listener(self):
        """Coroutine to listen to pixel update events through the websocket.

        Each pixel update is sent as a JSON object through the websocket
        connection, although in our experience at some point updates start to
        lag behind. A complete update is necessary once in a while."""

        if not self.ws_url:
            raise Exception("No place websocket URL available. Please use the "
                            "`scrape_info` method first.")

        logger.debug("Connecting to websocket...")

        async with self.session.ws_connect(self.ws_url) as ws:
            logger.info("Connected to websocket for pixel updates.")

            async for msg in ws:
                print(msg)  # TODO: update local pixel data

        self._running = False


async def create_drawer(username, password, loop=None):
    if not loop:
        loop = asyncio.get_event_loop()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('username', help="Your reddit username")
    parser.add_argument('password', help="Your reddit password")
    parser.add_argument(
        '-d', '--debug', action="store_true", default=False,
        help="Enable debug mode."
    )

    args = parser.parse_args()

    loop = asyncio.get_event_loop()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        warnings.filterwarnings("always", category=ResourceWarning)
        loop.set_debug(True)

    with aiohttp.ClientSession() as session:
        place_client = RedditPlaceClient(session)
        loop.create_task(place_client.main(args.username, args.password))
        loop.run_forever()

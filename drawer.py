"""
Headless reddit /r/place updater.

Written by /u/tr4ce.

Copyright (c) 2017 Lucas van Dijk

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import re
import json
import argparse
import logging
import warnings
import time
from datetime import datetime, timedelta

import asyncio
import aiohttp

MAX_QUEUE_SIZE = 25

REDDIT_LOGIN_URL = "https://www.reddit.com/post/login"
REDDIT_PLACE_URL = "https://www.reddit.com/place?webview=true"
REDDIT_GET_PIXEL_URL = "https://www.reddit.com/api/place/pixel.json"
REDDIT_DRAW_PIXEL_URL = "https://www.reddit.com/api/place/draw.json"

DRAWING_DATA_URL = (
    "https://raw.githubusercontent.com/Sadye/rPlace/master/data.json?"
    "no-cache={}"
)

logger = logging.getLogger(__name__)
logging.basicConfig()

modhash_regexp = re.compile(r'"modhash": "(\w+)",')
ws_url_regexp = re.compile(r'"place_websocket_url": "([^"]+?)",')


COLOUR_NAMES = [
    "wit",
    "lgrijs",
    "dgrijs",
    "zwart",
    "roze",
    "rood",
    "oranje",
    "bruin",
    "geel",
    "lgroen",
    "groen",
    "lblauw",
    "blauw",
    "dblauw",
    "magenta",
    "paars",
    "niets",
]


def get_colour_name(num):
    if not (0 <= num < 16):
        logger.debug("Unknown colour: %d", num)
        return "???"
    else:
        return COLOUR_NAMES[num]


class DrawingPlan:
    """Contains the data on what to draw."""

    def __init__(self, session):
        self.session = session

        self.start_x = 0
        self.start_y = 0
        self.width = 0
        self.height = 0
        self.colours = [[]]
        self.kill = False
        self.version = -1

        self.pixel_queue = []

    async def request(self):
        """Refresh drawing plan data."""

        current_time = int(time.time())
        url = DRAWING_DATA_URL.format(current_time)

        try:
            async with self.session.get(url) as resp:
                data = await resp.json(content_type=None)
                self.start_x = data['startX']
                self.start_y = data['startY']
                self.colours = data['colors']
                self.kill = data['kill']
                self.version = data['newVersion']

                self.height = len(self.colours)

                if self.height > 0:
                    self.width = max(len(row) for row in self.colours)
                else:
                    self.width = 0

                logger.debug("Succesfully updated drawing plan.")
                logger.debug("Start X: %d, start y: %d, kill: %s",
                             self.start_x, self.start_y, self.kill)

                return True
        except (aiohttp.ClientError, KeyError) as e:
            logger.exception(e)
            return False

    def within_area(self, x, y):
        return ((self.start_x <= x < self.start_x + self.width) and
                (self.start_y <= y < self.start_y + self.height) and
                self.get_colour(x, y) != -1)

    def get_colour(self, x, y):
        x2 = x - self.start_x
        y2 = y - self.start_y

        return self.colours[y2][x2]

    def check_pixel_update(self, x, y, new_colour):
        if not self.within_area(x, y):
            return False

        if self.get_colour(x, y) != new_colour:
            # Insert at beginning, newer pixel updates
            # have priority
            self.pixel_queue.insert(0, (x, y))

            logger.debug("Found wrong pixel update (new colour: %s, should be "
                         "%s), added to queue: %s",
                         get_colour_name(new_colour),
                         get_colour_name(self.get_colour(x, y)),
                         (x, y))

            if len(self.pixel_queue) > MAX_QUEUE_SIZE:
                self.pixel_queue.pop()

            return True
        else:
            logger.debug("Pixel %s update within our area with correct colour"
                         " %s.", (x, y), get_colour_name(new_colour))

        return False


class RedditPlaceClient:
    def __init__(self, session, loop=None):
        self.session = session
        self.modhash = ""
        self.ws_url = ""

        self.drawing_plan = DrawingPlan(session)
        self.pixel_queue = []

        if not loop:
            loop = asyncio.get_event_loop()
        self.loop = loop

    async def main_loop(self, username, password):
        logger.info("Start with login...")
        result = await self.login(username, password)

        if not result:
            logger.critical("Could not login on reddit.")
            return

        logger.info("Login succesfull")

        result = await self.scrape_info()
        if not result:
            logger.critical("Could not obtain modhash and websocket URL.")
            return

        logger.info("Scraped required information.")
        logger.debug("Modhash: %s", self.modhash)
        logger.debug("WS URL: %s", self.ws_url)

        logger.info("Download drawing plan...")
        await self.drawing_plan.request()
        logger.info("Done downloading data.")

        # Create the two other main tasks
        self.loop.create_task(self.pixel_update_listener())
        self.loop.create_task(self.data_updater())

        next_draw = datetime.now()
        while True:
            if next_draw <= datetime.now():
                # Time to draw a new pixel
                pixel = await self.search_for_pixel()
                if pixel:
                    x, y = pixel
                    new_colour = self.drawing_plan.get_colour(x, y)
                    result = await self.draw_pixel(x, y, new_colour)

                    if type(result) != bool:
                        # We got a response with a "wait_seconds" from the
                        # server, result is therefore an integer
                        next_draw = datetime.now() + timedelta(seconds=result)
                    elif not result:
                        # Something went wrong try again in 30 seconds
                        next_draw = datetime.now() + timedelta(seconds=30)
                    else:
                        # Succesful
                        next_draw = datetime.now() + timedelta(seconds=300)
                else:
                    # No pixel to draw, try in one minute again
                    logger.info("Empty pixel queue.")
                    next_draw = datetime.now() + timedelta(seconds=60)
            else:
                diff = next_draw - datetime.now()
                logger.info("Next action in %d seconds..", diff.seconds)
                logger.debug("Pixel queue length: %d",
                             len(self.drawing_plan.pixel_queue))

                await asyncio.sleep(10)

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
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except:
                        continue

                    if data['type'] == 'place':
                        x = data['payload']['x']
                        y = data['payload']['y']
                        colour = data['payload']['color']

                        self.drawing_plan.check_pixel_update(x, y, colour)

            logger.warning("Lost connection to websocket...")

    async def data_updater(self):
        """Repeatedly check for drawing plan updates.

        Should be scheduled once using `BaseEventLoop.create_task`."""

        while True:
            await asyncio.sleep(600)
            await self.drawing_plan.request()

    async def search_for_pixel(self):
        """Checks the pixel queue for pixels that are still wrongly coloured.
        It checks this by requesting the actual colour on the reddit server
        side, and if it's still wrong it returns this pixel.

        If something with the request goes wrong, this pixel is removed from
        the queue, and we give the reddit servers some breathing time."""

        while self.drawing_plan.pixel_queue:
            x, y = self.drawing_plan.pixel_queue.pop(0)

            # TODO: Local data check

            status, colour = await self.get_pixel_value_remote(x, y)

            # Server overloaded
            if status == 502:
                await asyncio.sleep(5)
                continue

            if colour == self.drawing_plan.get_colour(x, y):
                # Colour already correct on server side, skip and search for
                # a new one
                logger.info("Found a queued pixel %s which is already fixed on"
                            " the server side. Skipping.", (x, y))

                # Wait a second to not spam the reddit server
                await asyncio.sleep(2)
                continue
            else:
                return x, y

        return None

    async def get_pixel_value_remote(self, x, y):
        """Retreives the actual colour on the reddit server. Also returns the
        response status code to be able to act accordingly."""

        params = {'x': int(x), 'y': int(y)}
        async with self.session.get(REDDIT_GET_PIXEL_URL,
                                    params=params) as resp:
            if resp.status == 502:
                logger.info("Reddit API overloaded, no result.")
                return -1
            elif resp.status != 200:
                text = await resp.text()
                logger.warning("Could not get remote pixel value: %s", text)
                return -1

            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                logger.exception(e)
                return -1

            return resp.status, data['color']

    async def draw_pixel(self, x, y, new_colour):
        """Send a request to the reddit server to draw a pixel.

        When it returns a boolean, then it either went okay (True), or not
        (False). It is also possible to get an integer as return value, in that
        case it contains the number of seconds to wait before you can make
        another drawing request."""

        logger.info("Drawing a pixel at %s with new colour %s",
                    (x, y), get_colour_name(new_colour))

        headers = {
            'x-modhash': self.modhash
        }

        params = {
            'x': x,
            'y': y,
            'color': new_colour
        }

        async with self.session.post(REDDIT_DRAW_PIXEL_URL, headers=headers,
                                     data=params) as resp:
            if resp.status == 429:
                # We had to wait a bit longer...
                seconds = 60 * 5
                try:
                    data = await resp.json(content_type=None)
                    seconds = data['wait_seconds']
                except Exception as e:
                    logger.exception(e)

                logger.warning("We were to early to try drawing a pixel. "
                               "We need to wait another %d seconds before we "
                               "can draw a pixel.", seconds)

                return seconds
            elif resp.status != 200:
                text = await resp.text()
                logger.warning("Could not draw pixel: %s", text)
                return False
            else:
                return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('username', help="Your reddit username")
    parser.add_argument('password', help="Your reddit password")
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Enable verbose output, has two levels")

    args = parser.parse_args()

    loop = asyncio.get_event_loop()
    logging.getLogger().setLevel(logging.INFO)

    if args.verbose > 0:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.verbose > 1:
        warnings.filterwarnings("always", category=ResourceWarning)
        loop.set_debug(True)

    # Create HTTP session, also automatically stores any cookies created by
    # any request
    with aiohttp.ClientSession() as session:
        place_client = RedditPlaceClient(session)
        loop.create_task(place_client.main_loop(args.username, args.password))
        loop.run_forever()

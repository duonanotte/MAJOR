import os
import time
import json
import aiohttp
import re
import aiofiles
import asyncio
import random
import functools
import traceback

from urllib.parse import unquote
from http.cookiejar import unmatched
from typing import Callable, Tuple
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from pyrogram import Client
from pyrogram.errors import Unauthorized, UserDeactivated, AuthKeyUnregistered, FloodWait
from pyrogram.raw.functions.messages import RequestAppWebView
from pyrogram.raw.functions import account, messages
from pyrogram.raw.types import InputBotAppShortName, InputNotifyPeer, InputPeerNotifySettings
from bot.config import settings
from bot.utils import logger
from bot.exceptions import InvalidSession
from bot.utils.connection_manager import connection_manager
from .agents import generate_random_user_agent
from .headers import headers


def error_handler(func: Callable):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            await asyncio.sleep(1)
    return wrapper

class Tapper:
    def __init__(self, tg_client: Client, proxy: str):
        self.tg_client = tg_client
        self.session_name = tg_client.name
        self.proxy = proxy
        self.tg_web_data = None
        self.tg_client_id = 0

        self.user_agents_dir = "user_agents"
        self.session_ug_dict = {}
        self.headers = headers.copy()

    async def init(self):
        os.makedirs(self.user_agents_dir, exist_ok=True)
        await self.load_user_agents()
        user_agent, sec_ch_ua = await self.check_user_agent()
        self.headers['User-Agent'] = user_agent
        self.headers['Sec-Ch-Ua'] = sec_ch_ua

    async def generate_random_user_agent(self):
        user_agent, sec_ch_ua = generate_random_user_agent(device_type='android', browser_type='webview')
        return user_agent, sec_ch_ua

    async def load_user_agents(self) -> None:
        try:
            os.makedirs(self.user_agents_dir, exist_ok=True)
            filename = f"{self.session_name}.json"
            file_path = os.path.join(self.user_agents_dir, filename)

            if not os.path.exists(file_path):
                logger.info(f"{self.session_name} | User agent file not found. A new one will be created when needed.")
                return

            try:
                async with aiofiles.open(file_path, 'r') as user_agent_file:
                    content = await user_agent_file.read()
                    if not content.strip():
                        logger.warning(f"{self.session_name} | User agent file '{filename}' is empty.")
                        return

                    data = json.loads(content)
                    if data['session_name'] != self.session_name:
                        logger.warning(f"{self.session_name} | Session name mismatch in file '{filename}'.")
                        return

                    self.session_ug_dict = {self.session_name: data}
            except json.JSONDecodeError:
                logger.warning(f"{self.session_name} | Invalid JSON in user agent file: {filename}")
            except Exception as e:
                logger.error(f"{self.session_name} | Error reading user agent file {filename}: {e}")
        except Exception as e:
            logger.error(f"{self.session_name} | Error loading user agents: {e}")

    async def save_user_agent(self) -> Tuple[str, str]:
        user_agent_str, sec_ch_ua = await self.generate_random_user_agent()

        new_session_data = {
            'session_name': self.session_name,
            'user_agent': user_agent_str,
            'sec_ch_ua': sec_ch_ua
        }

        file_path = os.path.join(self.user_agents_dir, f"{self.session_name}.json")
        try:
            async with aiofiles.open(file_path, 'w') as user_agent_file:
                await user_agent_file.write(json.dumps(new_session_data, indent=4, ensure_ascii=False))
        except Exception as e:
            logger.error(f"{self.session_name} | Error saving user agent data: {e}")

        self.session_ug_dict = {self.session_name: new_session_data}

        logger.info(f"{self.session_name} | User agent saved successfully: {user_agent_str}")

        return user_agent_str, sec_ch_ua

    async def check_user_agent(self) -> Tuple[str, str]:
        if self.session_name not in self.session_ug_dict:
            return await self.save_user_agent()

        session_data = self.session_ug_dict[self.session_name]
        if 'user_agent' not in session_data or 'sec_ch_ua' not in session_data:
            return await self.save_user_agent()

        return session_data['user_agent'], session_data['sec_ch_ua']

    async def check_proxy(self, http_client: aiohttp.ClientSession) -> bool:
        try:
            response = await http_client.get(url='https://ipinfo.io/json', timeout=aiohttp.ClientTimeout(total=5))
            data = await response.json()

            ip = data.get('ip')
            city = data.get('city')
            country = data.get('country')

            logger.info(
                f"{self.session_name} | Check proxy! Country: <cyan>{country}</cyan> | City: <light-yellow>{city}</light-yellow> | Proxy IP: {ip}")

            return True

        except Exception as error:
            logger.error(f"{self.session_name} | Proxy error: {error}")
            return False

    async def get_tg_web_data(self) -> str:

        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            proxy_dict = dict(
                scheme=proxy.protocol,
                hostname=proxy.host,
                port=proxy.port,
                username=proxy.login,
                password=proxy.password
            )
        else:
            proxy_dict = None

        self.tg_client.proxy = proxy_dict

        try:
            if not self.tg_client.is_connected:
                try:
                    await self.tg_client.connect()

                except (Unauthorized, UserDeactivated, AuthKeyUnregistered):
                    raise InvalidSession(self.session_name)

            while True:
                try:
                    peer = await self.tg_client.resolve_peer('major')
                    break
                except FloodWait as fl:
                    fls = fl.value

                    logger.warning(f"{self.session_name} | FloodWait {fl}")
                    wait_time = random.randint(3600, 12800)
                    logger.info(f"{self.session_name} | Sleep {wait_time}s")
                    await asyncio.sleep(wait_time)

            ref_id = settings.REF_ID if random.randint(0, 100) <= 85 else "339631649"

            web_view = await self.tg_client.invoke(messages.RequestAppWebView(
                peer=peer,
                app=InputBotAppShortName(bot_id=peer, short_name="start"),
                platform='android',
                write_allowed=True,
                start_param=ref_id
            ))

            auth_url = web_view.url
            tg_web_data = unquote(string=auth_url.split('tgWebAppData=')[1].split('&tgWebAppVersion')[0])

            me = await self.tg_client.get_me()
            self.tg_client_id = me.id

            if self.tg_client.is_connected:
                await self.tg_client.disconnect()

            return ref_id, tg_web_data

        except InvalidSession as error:
            logger.error(f"{self.session_name} | Invalid session")
            await asyncio.sleep(delay=3)
            return None, None

        except Exception as error:
            logger.error(f"{self.session_name} | Unknown error: {error}")
            await asyncio.sleep(delay=3)
            return None, None

    @error_handler
    async def make_request(self, http_client, method, endpoint=None, url=None, **kwargs):
        full_url = url or f"https://major.bot/api{endpoint or ''}"
        response = await http_client.request(method, full_url, **kwargs)
        response.raise_for_status()
        return await response.json()

    @error_handler
    async def login(self, http_client, init_data, ref_id):
        response = await self.make_request(http_client, 'POST', endpoint="/auth/tg/", json={"init_data": init_data})
        if response and response.get("access_token", None):
            return response
        return None

    @error_handler
    async def get_daily(self, http_client):
        return await self.make_request(http_client, 'GET', endpoint="/tasks/?is_daily=true")

    @error_handler
    async def get_tasks(self, http_client):
        return await self.make_request(http_client, 'GET', endpoint="/tasks/?is_daily=false")

    @error_handler
    async def done_tasks(self, http_client, task_id):
        return await self.make_request(http_client, 'POST', endpoint="/tasks/", json={"task_id": task_id})

    @error_handler
    async def claim_swipe_coins(self, http_client):
        response = await self.make_request(http_client, 'GET', endpoint="/swipe_coin/")
        if response and response.get('success') is True:
            logger.info(f"{self.session_name} | Start game <y>SwipeCoins</y>")
            coins = random.randint(settings.SWIPE_COIN[0], settings.SWIPE_COIN[1])
            payload = {"coins": coins}
            await asyncio.sleep(55)
            response = await self.make_request(http_client, 'POST', endpoint="/swipe_coin/", json=payload)
            if response and response.get('success') is True:
                return coins
            return 0
        return 0

    @error_handler
    async def claim_hold_coins(self, http_client):
        response = await self.make_request(http_client, 'GET', endpoint="/bonuses/coins/")
        if response and response.get('success') is True:
            logger.info(f"{self.session_name} | Start game <y>HoldCoins</y>")
            coins = random.randint(settings.HOLD_COIN[0], settings.HOLD_COIN[1])
            payload = {"coins": coins}
            await asyncio.sleep(55)
            response = await self.make_request(http_client, 'POST', endpoint="/bonuses/coins/", json=payload)
            if response and response.get('success') is True:
                return coins
            return 0
        return 0

    @error_handler
    async def claim_roulette(self, http_client):
        response = await self.make_request(http_client, 'GET', endpoint="/roulette/")
        if response and response.get('success') is True:
            logger.info(f"{self.session_name} | Start game <y>Roulette</y>")
            await asyncio.sleep(10)
            response = await self.make_request(http_client, 'POST', endpoint="/roulette/")
            if response:
                return response.get('rating_award', 0)
            return 0
        return 0

    @error_handler
    async def visit(self, http_client):
        return await self.make_request(http_client, 'POST', endpoint="/user-visits/visit/?")

    @error_handler
    async def streak(self, http_client):
        return await self.make_request(http_client, 'POST', endpoint="/user-visits/streak/?")

    @error_handler
    async def get_detail(self, http_client):
        detail = await self.make_request(http_client, 'GET', endpoint=f"/users/{self.tg_client_id}/")

        return detail.get('rating') if detail else 0

    @error_handler
    async def leave_squad(self, http_client, squad_id):
        return await self.make_request(http_client, 'POST', endpoint=f"/squads/leave/")

    @error_handler
    async def join_squad(self, http_client, squad_id):
        return await self.make_request(http_client, 'POST', endpoint=f"/squads/{squad_id}/join/?")

    @error_handler
    async def get_squad(self, http_client, squad_id):
        return await self.make_request(http_client, 'GET', endpoint=f"/squads/{squad_id}?")

    @error_handler
    async def youtube_answers(self, http_client, task_id, task_title):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                    "https://raw.githubusercontent.com/GravelFire/TWFqb3JCb3RQdXp6bGVEdXJvdg/master/answer.py") as response:
                status = response.status
                if status == 200:
                    response_data = json.loads(await response.text())
                    youtube_answers = response_data.get('youtube', {})
                    if task_title in youtube_answers:
                        answer = youtube_answers[task_title]
                        payload = {
                            "task_id": task_id,
                            "payload": {
                                "code": answer
                            }
                        }
                        logger.info(f"{self.session_name} | Attempting YouTube task: <y>{task_title}</y>")
                        response = await self.make_request(http_client, 'POST', endpoint="/tasks/", json=payload)
                        if response and response.get('is_completed') is True:
                            logger.info(f"{self.session_name} | Completed YouTube task: <y>{task_title}</y>")
                            return True
        return False

    # @error_handler
    async def run(self) -> None:
        if settings.USE_RANDOM_DELAY_IN_RUN:
            random_delay = random.randint(settings.RANDOM_DELAY_IN_RUN[0], settings.RANDOM_DELAY_IN_RUN[1])
            logger.info(
                f"{self.session_name} | The Bot will go live in <y>{random_delay}s</y>")
            await asyncio.sleep(random_delay)

        await self.init()

        proxy_conn = ProxyConnector().from_url(self.proxy) if self.proxy else None
        http_client = aiohttp.ClientSession(headers=self.headers, connector=proxy_conn)
        connection_manager.add(http_client)

        if settings.USE_PROXY:
            if not self.proxy:
                logger.error(f"{self.session_name} | Proxy is not set. Aborting operation.")
                return
            if not await self.check_proxy(http_client):
                logger.error(f"{self.session_name} | Proxy check failed. Aborting operation.")
                return

        ref_id, init_data = await self.get_tg_web_data()

        while True:
            try:
                if http_client.closed:
                    if proxy_conn:
                        if not proxy_conn.closed:
                            await proxy_conn.close()

                    proxy_conn = ProxyConnector().from_url(self.proxy) if self.proxy else None
                    http_client = aiohttp.ClientSession(headers=self.headers, connector=proxy_conn)
                    connection_manager.add(http_client)

                    ref_id, init_data = await self.get_tg_web_data()

                user_data = await self.login(http_client=http_client, init_data=init_data, ref_id=ref_id)
                if not user_data:
                    logger.info(f"{self.session_name} | <r>Failed login</r>")
                    sleep_time = random.randint(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])
                    logger.info(f"{self.session_name} | Sleep <y>{sleep_time}s</y>")
                    await asyncio.sleep(delay=sleep_time)
                    continue

                http_client.headers['Authorization'] = "Bearer " + user_data.get("access_token")
                self.headers['Authorization'] = "Bearer " + user_data.get("access_token")
                logger.info(f"{self.session_name} | <y>⭐ Login successfully!</y>")
                user = user_data.get('user')
                squad_id = user.get('squad_id')
                rating = await self.get_detail(http_client=http_client)
                logger.info(f"{self.session_name} | Points : <y>{rating:,}</y>")

                if squad_id is None:
                    await self.join_squad(http_client=http_client, squad_id=settings.SQUAD_ID_JOIN)
                    squad_id = random.choice(settings.SQUAD_ID_JOIN)
                    await asyncio.sleep(1)

                if squad_id == settings.SQUAD_ID_LEAVE:
                    await self.leave_squad(http_client=http_client, squad_id=squad_id)
                    await asyncio.sleep(random.randint(5, 7))
                    new_squad_id = random.choice(settings.SQUAD_ID_JOIN)
                    await self.join_squad(http_client=http_client, squad_id=new_squad_id)
                    squad_id = new_squad_id
                    await asyncio.sleep(1)

                logger.info(f"{self.session_name} | Squad ID: <y>{squad_id}</y>")
                data_squad = await self.get_squad(http_client=http_client, squad_id=squad_id)
                if data_squad:
                    logger.info(
                        f"{self.session_name} | Squad : <y>{data_squad.get('name')}</y>")

                data_visit = await self.visit(http_client=http_client)
                if data_visit:
                    await asyncio.sleep(1)
                    logger.info(f"{self.session_name} | Daily Streak : <y>{data_visit.get('streak')}</y>")

                await self.streak(http_client=http_client)

                tasks = [
                    ('HoldCoins', self.claim_hold_coins),
                    ('SwipeCoins', self.claim_swipe_coins),
                    ('Roulette', self.claim_roulette),
                    ('d_tasks', self.get_daily),
                    ('m_tasks', self.get_tasks)
                ]

                random.shuffle(tasks)

                for task_name, task_func in tasks:

                    # logger.info(f"{self.session_name} | Task <y>{task_name}</y>")

                    if task_name in ['HoldCoins', 'SwipeCoins', 'Roulette', 'Puzzle']:
                        result = await task_func(http_client=http_client)
                        if result:
                            await asyncio.sleep(1)
                            reward = "+5000⭐" if task_name == 'Puzzle' else f"+{result} coin(s)"
                            logger.info(f"{self.session_name} | Reward {task_name}: <y>{reward}</y>")
                        await asyncio.sleep(10)

                    elif task_name == 'd_tasks':
                        data_daily = await task_func(http_client=http_client)
                        if data_daily:
                            random.shuffle(data_daily)
                            for daily in data_daily:
                                await asyncio.sleep(10)
                                id = daily.get('id')
                                title = daily.get('title')
                                data_done = await self.done_tasks(http_client=http_client, task_id=id)
                                if data_done and data_done.get('is_completed') is True:
                                    await asyncio.sleep(1)
                                    logger.info(
                                        f"{self.session_name} | Daily Task : <y>{daily.get('title')}</y> | Reward : <y>{daily.get('award')}</y>")


                    elif task_name == 'm_tasks':
                        data_task = await task_func(http_client=http_client)
                        if data_task:
                            random.shuffle(data_task)
                            for task in data_task:
                                await asyncio.sleep(10)
                                id = task.get('id')
                                title = task.get("title", "")
                                if task.get("type") == "code":
                                    await self.youtube_answers(http_client=http_client, task_id=id, task_title=title)
                                    continue

                                data_done = await self.done_tasks(http_client=http_client, task_id=id)
                                if data_done and data_done.get('is_completed') is True:
                                    await asyncio.sleep(1)
                                    logger.info(
                                        f"{self.session_name} | Task : <y>{title}</y> | Reward : <y>{task.get('award')}</y>")



            except aiohttp.ClientConnectorError as error:
                delay = random.randint(1800, 3600)
                logger.error(f"{self.session_name} | Connection error: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except aiohttp.ServerDisconnectedError as error:
                delay = random.randint(900, 1800)
                logger.error(f"{self.session_name} | Server disconnected: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except aiohttp.ClientResponseError as error:
                delay = random.randint(3600, 7200)
                logger.error(
                   f"{self.session_name} | HTTP response error: {error}. Status: {error.status}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except aiohttp.ClientError as error:
                delay = random.randint(3600, 7200)
                logger.error(f"{self.session_name} | HTTP client error: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except asyncio.TimeoutError:
                delay = random.randint(7200, 14400)
                logger.error(f"{self.session_name} | Request timed out. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except InvalidSession as error:
                logger.critical(f"{self.session_name} | Invalid Session: {error}. Manual intervention required.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                raise error


            except json.JSONDecodeError as error:
                delay = random.randint(1800, 3600)
                logger.error(f"{self.session_name} | JSON decode error: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)

            except KeyError as error:
                delay = random.randint(1800, 3600)
                logger.error(
                    f"{self.session_name} | Key error: {error}. Possible API response change. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)


            except Exception as error:
                delay = random.randint(7200, 14400)
                logger.error(f"{self.session_name} | Unexpected error: {error}. Retrying in {delay} seconds.")
                logger.debug(f"Full error details: {traceback.format_exc()}")
                await asyncio.sleep(delay)

            finally:
                await http_client.close()
                if proxy_conn:
                    if not proxy_conn.closed:
                        proxy_conn.close()
                connection_manager.remove(http_client)

                sleep_time = random.randint(settings.SLEEP_TIME[0], settings.SLEEP_TIME[1])
                hours = int(sleep_time // 3600)
                minutes = (int(sleep_time % 3600)) // 60
                logger.info(
                    f"{self.session_name} | Sleep <yellow>{hours} hours</yellow> and <yellow>{minutes} minutes</yellow>")
                await asyncio.sleep(delay=sleep_time)
            

async def run_tapper(tg_client: Client, proxy: str | None):
    session_name = tg_client.name
    if settings.USE_PROXY and not proxy:
        logger.error(f"{session_name} | No proxy found for this session")
        return
    try:
        await Tapper(tg_client=tg_client, proxy=proxy).run()
    except InvalidSession:
        logger.error(f"{session_name} | Invalid Session")

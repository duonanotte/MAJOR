import asyncio
import random
import logging
import traceback
import json
import aiohttp
from bot.utils.logger import logger

class ErrorHandler:
    def init(self, session_name):
        self.session_name = session_name
        self.error_counts = {}

    async def handle(self, error):
        error_type = type(error).name
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1

        if isinstance(error, aiohttp.ClientConnectorError):
            delay = random.randint(300, 600)  # 5-10 минут
        elif isinstance(error, aiohttp.ServerDisconnectedError):
            delay = random.randint(180, 360)  # 3-6 минут
        elif isinstance(error, aiohttp.ClientResponseError):
            delay = self._handle_response_error(error)
        elif isinstance(error, aiohttp.ClientError):
            delay = random.randint(600, 1200)  # 10-20 минут
        elif isinstance(error, asyncio.TimeoutError):
            delay = random.randint(900, 1800)  # 15-30 минут
        elif isinstance(error, json.JSONDecodeError):
            delay = random.randint(300, 600)  # 5-10 минут
        elif isinstance(error, KeyError):
            delay = random.randint(1800, 3600)  # 30-60 минут
        else:
            delay = random.randint(3600, 7200)  # 1-2 часа

        if self.error_counts[error_type] > 3:
            delay *= 2

        logger.error(f"{self.session_name} | {error_type}: {str(error)}. Retrying in {delay} seconds.")
        logger.debug(f"Full error details: {traceback.format_exc()}")

        await asyncio.sleep(delay)
        return delay

    def _handle_response_error(self, error):
        if error.status in [429, 503]:  # Too Many Requests or Service Unavailable
            return random.randint(1800, 3600)  # 30-60 минут
        elif error.status >= 500:  # Server errors
            return random.randint(900, 1800)  # 15-30 минут
        else:  # Other client errors
            return random.randint(300, 600)  # 5-10 минут


async def handle_error(error, session_name):
    handler = ErrorHandler(session_name)
    return await handler.handle(error)
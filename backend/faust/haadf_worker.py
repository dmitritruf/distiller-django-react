import asyncio
import logging
import os
import sys
import tempfile

import aiohttp
import matplotlib.pyplot as plt
import ncempy.io as nio
from aiopath import AsyncPath

import faust
from config import settings
from constants import TOPIC_HAADF_FILE_EVENTS

# Setup logger
logger = logging.getLogger("haadf_worker")
logger.setLevel(logging.INFO)

app = faust.App(
    "still", store="rocksdb://", broker=settings.KAFKA_URL, topic_partitions=1
)


class HaadfEvent(faust.Record):
    path: str
    scan_id: int


haadf_events_topic = app.topic(TOPIC_HAADF_FILE_EVENTS, value_type=HaadfEvent)


async def generate_haadf_image(tmp_dir: str, dm4_path: str, scan_id: int) -> AsyncPath:
    haadf = nio.read(dm4_path)
    img = haadf["data"]
    # TODO push to scan
    # haadf['pixelSize'] # this contains the real space pixel size (most important meta data)
    path = AsyncPath(tmp_dir) / f"{scan_id}.png"

    # Work around issue with how faust resets sys.stdout to an instance of FileLogProxy
    # which doesn't have the property buffer, which is check by Pillow when its writing
    # out the image, so just reset it to the real stdout while calling imsave.
    stdout = sys.stdout
    sys.stdout = sys.__stdout__
    plt.imsave(str(path), img)
    sys.stdout = stdout

    return path


async def upload_haadf_image(session: aiohttp.ClientSession, path: AsyncPath):
    # Now upload
    async with path.open("rb") as fp:
        headers = {settings.API_KEY_NAME: settings.API_KEY}
        data = aiohttp.FormData()
        data.add_field("file", fp, filename=path.name, content_type="image/png")

        return await session.post(
            f"{settings.API_URL}/files/haadf", headers=headers, data=data
        )


@app.agent(haadf_events_topic)
async def watch_for_haadf_events(haadf_events):

    async with aiohttp.ClientSession() as session:
        async for event in haadf_events:
            path = event.path
            scan_id = event.scan_id
            with tempfile.TemporaryDirectory() as tmp:
                image_path = await generate_haadf_image(tmp, path, scan_id)
                r = await upload_haadf_image(session, image_path)
                r.raise_for_status()

            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, os.remove, path)
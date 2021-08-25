import asyncio
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Union

import aiohttp
import httpx
import jinja2
from aiopath import AsyncPath
from authlib.integrations.httpx_client.oauth2_client import AsyncOAuth2Client
from authlib.oauth2.rfc7523 import PrivateKeyJWT

import faust
from config import settings
from constants import (COUNT_JOB_SCRIPT_TEMPLATE, DW_JOB_STRIPED_VAR,
                       SFAPI_BASE_URL, SFAPI_TOKEN_URL, SLURM_RUNNING_STATES,
                       TOPIC_JOB_SUBMIT_EVENTS, TRANSFER_JOB_SCRIPT_TEMPLATE)
from schemas import JobUpdate, Location, Scan, SfapiJob
from utils import get_job
from utils import update_job as update_job_request

# Setup logger
logger = logging.getLogger("job_worker")
logger.setLevel(logging.INFO)

app = faust.App(
    "still", store="rocksdb://", broker=settings.KAFKA_URL, topic_partitions=1
)


client = AsyncOAuth2Client(
    client_id=settings.SFAPI_CLIENT_ID,
    client_secret=settings.SFAPI_PRIVATE_KEY,
    token_endpoint_auth_method=PrivateKeyJWT(SFAPI_TOKEN_URL),
    grant_type=settings.SFAPI_GRANT_TYPE,
    token_endpoint=SFAPI_TOKEN_URL,
    timeout=10.0,
)

loop = asyncio.get_event_loop()
loop.run_until_complete(client.fetch_token())


class JobType(str, Enum):
    COUNT = "count"
    TRANSFER = "transfer"

    def __str__(self) -> str:
        return self.value


class Location(faust.Record):
    host: str
    path: str


class Scan(faust.Record):
    id: int
    log_files: int
    locations: List[Location]


class Job(faust.Record):
    id: int
    job_type: JobType


class SubmitJobEvent(faust.Record):
    job: Job
    scan: Scan


submit_job_events_topic = app.topic(TOPIC_JOB_SUBMIT_EVENTS, value_type=SubmitJobEvent)


async def render_job_script(scan: Scan, job: Job) -> str:
    if job.job_type == JobType.COUNT:
        dest_dir = DW_JOB_STRIPED_VAR
        template_name = COUNT_JOB_SCRIPT_TEMPLATE
    else:
        dest_dir = settings.JOB_NCEMHUB_RAW_DATA_PATH
        template_name = TRANSFER_JOB_SCRIPT_TEMPLATE

    template_loader = jinja2.FileSystemLoader(
        searchpath=Path(__file__).parent / "templates"
    )
    template_env = jinja2.Environment(loader=template_loader, enable_async=True)
    template = template_env.get_template(template_name)
    output = await template.render_async(
        settings=settings, scan=scan, dest_dir=dest_dir, job=job, **job.params
    )

    logger.info(output)

    return output


async def sfapi_get(url: str, params: Dict[str, Any] = {}) -> httpx.Response:
    await client.ensure_active_token()

    r = await client.get(
        f"{SFAPI_BASE_URL}/{url}",
        headers={
            "Authorization": client.token["access_token"],
            "accept": "application/json",
        },
        params=params,
    )
    r.raise_for_status()

    return r


async def sfapi_post(url: str, data: Dict[str, Any]) -> httpx.Response:
    await client.ensure_active_token()

    r = await client.post(
        f"{SFAPI_BASE_URL}/{url}",
        headers={
            "Authorization": client.token["access_token"],
            "accept": "application/json",
        },
        data=data,
    )
    r.raise_for_status()

    return r


class SfApiError(Exception):
    def __init__(self, message):
        self.message = message


async def submit_job(batch_submit_file: str) -> int:
    data = {"job": batch_submit_file, "isPath": True}

    r = await sfapi_post("compute/jobs/cori", data)
    r.raise_for_status()

    sfapi_response = r.json()
    if sfapi_response["status"] != "ok":
        raise SfApiError(sfapi_response["error"])

    task_id = sfapi_response["task_id"]

    # We now need to poll waiting for the task to complete!
    while True:
        r = await sfapi_get(f"tasks/{task_id}")
        r.raise_for_status()

        sfapi_response = r.json()

        if sfapi_response["status"] == "error":
            raise SfApiError(sfapi_response["error"])

        logger.info(sfapi_response)

        if sfapi_response.get("result") is None:
            await asyncio.sleep(1)
            continue

        results = json.loads(sfapi_response["result"])

        if results["status"] == "error":
            raise SfApiError(results["error"])

        slurm_id = results.get("jobid")
        if slurm_id is None:
            raise SfApiError(f"Unable to extract slurm job if for task: {task_id}")

        return int(slurm_id)


async def update_slurm_job_id(
    session: aiohttp.ClientSession, job_id: int, slurm_id: int
) -> None:
    update = JobUpdate(id=job_id, slurm_id=slurm_id)
    await update_job_request(session, update)


async def process_submit_job_event(
    session: aiohttp.ClientSession, event: SubmitJobEvent
) -> None:
    # Render the script
    output = await render_job_script(scan=event.scan, job=event.job)
    submission_script_path = (
        AsyncPath(settings.JOB_SCRIPT_DIRECTORY)
        / str(event.job.id)
        / f"{event.job.job_type}-{event.job.id}.sh"
    )

    await submission_script_path.parent.mkdir(parents=True, exist_ok=False)

    async with submission_script_path.open("w") as fp:
        await fp.write(output)

    # Submit the job
    slurm_id = await submit_job(str(submission_script_path))

    # Update the job model with the slurm id
    await update_slurm_job_id(session, event.job.id, slurm_id)


@app.agent(submit_job_events_topic)
async def watch_for_submit_job_events(submit_jobs_events):
    async with aiohttp.ClientSession() as session:
        async for event in submit_jobs_events:
            try:
                await process_submit_job_event(session, event)
            except SfApiError as ex:
                logger.error(f"Error submitting job: {ex.message}")


async def update_job(
    session: aiohttp.ClientSession, job_id: int, state: str, output: str = None
) -> None:
    update = JobUpdate(id=job_id, state=state, output=output)
    await update_job_request(session, update)


def extract_jobs(sfapi_response: dict) -> List[SfapiJob]:
    jobs = []
    for job in sfapi_response["output"]:
        jobs.append(SfapiJob(workdir=job["workdir"], state=job["state"], name=job["jobname"], slurm_id=int(job["jobid"])))

    return jobs


def extract_job_id(workdir: str) -> Union[int, None]:
    try:
        id = int(AsyncPath(workdir).name)
        return id
    except ValueError:
        return None


async def read_slurm_out(slurm_id: int, workdir: str) -> str:
    out_file_path = AsyncPath(workdir) / f"slurm-{slurm_id}.out"
    if await out_file_path.exists():
        logger.info("Output exists", str(out_file_path))
        async with out_file_path.open("r") as fp:
            return await fp.read()

    return None


@app.timer(interval=60)
async def monitor_jobs():
    async with aiohttp.ClientSession() as session:
        try:
            params = {"kwargs": [f"user={settings.SFAPI_USER}"], "sacct": True}

            logger.info("monitor jobs")
            r = await sfapi_get("compute/jobs/cori", params)
            r.raise_for_status()

            response_json = r.json()

            if response_json["status"] != "ok":
                error = response_json["error"]
                logger.warning(f"SFAPI request to fetch jobs failed with: {error}")
                return

            logger.info(response_json)

            jobs = extract_jobs(response_json)
            for job in jobs:
                id = extract_job_id(job.workdir)
                if id is None:
                    logger.warning(
                        f"Unable to extract job id from workdir: {job.workdir}"
                    )
                    continue

                # We are done upload the output
                output = None
                if job.state not in SLURM_RUNNING_STATES:
                    output = await read_slurm_out(
                        job.slurm_id, job.workdir
                    )

                await update_job(session, id, job.state, output=output)
        except httpx.ReadTimeout as ex:
            logger.warning("Job monitoring request timed out", ex)
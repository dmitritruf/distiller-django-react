import logging
import re
from datetime import datetime
from typing import List

import aiohttp

import faust
from config import settings
from constants import (FILE_EVENT_TYPE_CLOSED, FILE_EVENT_TYPE_CREATED,
                       FILE_EVENT_TYPE_DELETED, FILE_EVENT_TYPE_MODIFIED,
                       PRIMARY_LOG_FILE_REGEX, TOPIC_FILE_EVENTS,
                       TOPIC_SCAN_EVENTS, TOPIC_SYNC_EVENTS)
from schemas import ScanCreate, ScanUpdate
from utils import create_scan, extract_scan_id, get_scans, update_scan

# Setup logger
logger = logging.getLogger("worker")
logger.setLevel(logging.INFO)

app = faust.App(
    "still", store="rocksdb://", broker=settings.KAFKA_URL, topic_partitions=1
)


class FileSystemEvent(faust.Record):
    event_type: str
    src_path: str
    is_directory: bool
    created: datetime


file_events_topic = app.topic(TOPIC_FILE_EVENTS, value_type=FileSystemEvent)


class ScanEvent(faust.Record):
    scan_id: int
    log_files: int


scan_events_topic = app.topic(TOPIC_SCAN_EVENTS, value_type=ScanEvent)


class File(faust.Record):
    path: str
    created: datetime


class SyncEvent(faust.Record):
    files: List[File]


sync_events_topic = app.topic(TOPIC_SYNC_EVENTS, value_type=SyncEvent)


class LogFileState(faust.Record):
    received_created_event: bool = False
    received_closed_event: bool = False
    created: datetime = None
    processed: bool = False


# path to log file state
log_files = app.Table("log_files", default=LogFileState)
# native scan id to db id
scan_id_to_id = app.Table("scan_id_to_id", default=int)
# scan id to list of processed log files paths
scan_id_to_log_files = app.Table("scan_id_to_log_files", default=list)


def scan_complete(scan_log_files: List[str]):
    return len(scan_log_files) == 72


async def process_delete_event(path: str) -> None:
    scan_id = extract_scan_id(path)
    del log_files[path]
    scan_log_files = scan_id_to_log_files[scan_id]
    scan_log_files.remove(path)

    # If all the log file are gone then remove the scan
    if not scan_log_files:
        del scan_id_to_id[scan_id]
        del scan_id_to_log_files[scan_id]
        logger.info(f"Scan {scan_id} removed.")
    else:
        scan_id_to_log_files[scan_id] = scan_log_files


async def process_log_file(
    session: aiohttp.ClientSession, event: FileSystemEvent
) -> None:
    path = event.src_path
    scan_id = extract_scan_id(path)

    # Update list of log file for the scan
    scan_log_files = scan_id_to_log_files[scan_id]
    scan_log_files.append(path)
    scan_log_files = set(scan_log_files)
    scan_id_to_log_files[scan_id] = scan_log_files

    primary_log_file = re.match(PRIMARY_LOG_FILE_REGEX, path)

    # If this is the primary log file for the scan ( the one we use the timestamp from )
    # then check if we have a scan and create one if necessary
    if primary_log_file:
        # First check if we already have a scan
        scans = await get_scans(session, scan_id=scan_id, created=event.created)
        if len(scans) > 1:
            raise Exception("Multiple scans with the same id and creation time!")

        if len(scans) == 0:
            scan = await create_scan(
                session,
                ScanCreate(
                    scan_id=scan_id,
                    created=event.created,
                    logs_files=len(scan_log_files),
                ),
            )
            scan_id_to_id[scan_id] = scan.id
        else:
            scan = scans[0]
            scan_id_to_id[scan_id] = scan.id

    scan_event = ScanEvent(scan_id, len(scan_log_files))
    await scan_events_topic.send(value=scan_event)

    if scan_id in scan_id_to_id:
        await update_scan(
            session,
            ScanUpdate(id=scan_id_to_id[scan_id], log_files=len(scan_log_files)),
        )

    if scan_complete(scan_log_files):
        logger.info(f"Transfer complete for scan {scan_id}")


def is_override(event: FileSystemEvent, state: LogFileState) -> bool:
    return state.created is not None and state.created != event.created


async def process_override(event: FileSystemEvent) -> None:
    scan_id = extract_scan_id(event.src_path)
    del scan_id_to_id[scan_id]
    del scan_id_to_log_files[scan_id]
    for p in log_files.keys():
        if scan_id == extract_scan_id(p):
            del log_files[p]


@app.agent(file_events_topic)
async def watch_for_logs(file_events):
    async with aiohttp.ClientSession() as session:
        async for event in file_events:
            path = event.src_path
            event_type = event.event_type

            # Skip event we are not interested in
            if (
                event_type
                not in [
                    FILE_EVENT_TYPE_CREATED,
                    FILE_EVENT_TYPE_CLOSED,
                    FILE_EVENT_TYPE_DELETED,
                    FILE_EVENT_TYPE_MODIFIED,
                ]
                or event.is_directory
            ):
                continue

            # Handle delete
            if event_type == FILE_EVENT_TYPE_DELETED:
                await process_delete_event(path)
                continue

            # Check if we have already processed this log file.
            state = log_files[path]
            if state.processed and state.created == event.created:
                continue

            # We are seeing a scan being overridden
            if is_override(event, state):
                await process_override(event)

            state.created = event.created
            if event_type in [FILE_EVENT_TYPE_CREATED, FILE_EVENT_TYPE_MODIFIED]:
                state.received_created_event = True
            elif event_type == FILE_EVENT_TYPE_CLOSED:
                state.received_closed_event = True

            # We have seen the right events process the logfile
            if state.received_created_event and state.received_created_event:
                await process_log_file(session, event)

                state.processed = True

            # Ensure changelog is updated
            log_files[path] = state


async def process_sync_event(session: aiohttp.ClientSession, event: SyncEvent) -> None:
    # Handle deleted log files
    log_file_paths = [f.path for f in event.files]
    for f in log_files.keys():
        if f not in log_file_paths:
            await process_delete_event(f)

    for f in event.files:
        path = f.path
        state = log_files[f.path]
        # Skip over anything that has already been proccessed
        if state.processed and state.created == f.created:
            continue

        file_event = FileSystemEvent(
            src_path=f.path,
            created=f.created,
            event_type=FILE_EVENT_TYPE_CREATED,
            is_directory=False,
        )
        # We are seeing a scan being overridden
        if is_override(file_event, state):
            await process_override(file_event)

        await process_log_file(session, file_event)

        state.created = f.created
        state.received_created_event = True
        state.received_closed_event = True
        state.processed = True

        # Ensure changelog is updated
        log_files[path] = state


@app.agent(sync_events_topic)
async def watch_for_sync_event(sync_events):
    async with aiohttp.ClientSession() as session:
        async for event in sync_events:
            await process_sync_event(session, event)

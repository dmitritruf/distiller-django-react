from datetime import datetime
from typing import List, Tuple, Union

from sqlalchemy import desc, or_, update
from sqlalchemy.orm import Session

from app import models, schemas
from app.core import constants


def get_scan(db: Session, id: int):
    return db.query(models.Scan).filter(models.Scan.id == id).first()


def get_scan_by_scan_id(db: Session, scan_id: int):
    return db.query(models.Scan).filter(models.Scan.scan_id == scan_id).first()


def _get_scans_query(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    scan_id: int = -1,
    state: schemas.ScanState = None,
    created: datetime = None,
    created_since: datetime = None,
    has_haadf: bool = None,
):
    query = db.query(models.Scan)
    if scan_id > -1:
        query = query.filter(models.Scan.scan_id == scan_id)

    if state is not None:
        if state == schemas.ScanState.TRANSFER:
            query = query.filter(models.Scan.log_files < constants.NUMBER_OF_LOG_FILES)
        elif state == schemas.ScanState.COMPLETE:

            query = query.filter(models.Scan.log_files == constants.NUMBER_OF_LOG_FILES)

    if created is not None:
        query = query.filter(models.Scan.created == created)

    if created_since is not None:
        query = query.filter(models.Scan.created > created_since)

    if has_haadf is not None:
        if has_haadf:
            query = query.filter(models.Scan.haadf_path != None)
        else:
            query = query.filter(models.Scan.haadf_path == None)

    return query


def get_scans(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    scan_id: int = -1,
    state: schemas.ScanState = None,
    created: datetime = None,
    created_since: datetime = None,
    has_haadf: bool = None,
):
    query = _get_scans_query(
        db, skip, limit, scan_id, state, created, created_since, has_haadf
    )

    return query.order_by(desc(models.Scan.created)).offset(skip).limit(limit).all()


def get_scans_count(
    db: Session,
    skip: int = 0,
    limit: int = 100,
    scan_id: int = -1,
    state: schemas.ScanState = None,
    created: datetime = None,
    has_haadf: bool = None,
):
    query = _get_scans_query(db, skip, limit, scan_id, state, created, has_haadf)

    return query.count()


def create_scan(db: Session, scan: schemas.ScanCreate, haadf_path: str = None):
    locations = scan.locations
    scan.locations = []

    db_scan = models.Scan(**scan.dict(), haadf_path=haadf_path)
    db.add(db_scan)
    for l in locations:
        l = models.Location(**l.dict())
        db_scan.locations.append(l)
        db.add(l)
    db.commit()
    db.refresh(db_scan)

    return db_scan


def update_scan(
    db: Session,
    id: int,
    log_files: int = None,
    locations: List[schemas.Location] = None,
    haadf_path: str = None,
    notes: str = None,
):
    updated = False

    if log_files is not None:
        statement = (
            update(models.Scan)
            .where(models.Scan.id == id)
            .where(models.Scan.log_files < log_files)
            .values(log_files=log_files)
        )

        resultsproxy = db.execute(statement)
        log_files_updated = resultsproxy.rowcount == 1
        updated = updated or log_files_updated

    if locations is not None:
        locations_updated = False
        for l in locations:
            exists = (
                db.query(models.Location.id)
                .filter_by(scan_id=id, host=l.host, path=l.path)
                .first()
                is not None
            )
            if not exists:
                l = models.Location(**l.dict(), scan_id=id)
                db.add(l)
                locations_updated = True

        updated = updated or locations_updated

    if haadf_path is not None:
        statement = (
            update(models.Scan)
            .where(models.Scan.id == id)
            .where(
                or_(
                    models.Scan.haadf_path != haadf_path, models.Scan.haadf_path == None
                )
            )
            .values(haadf_path=haadf_path)
        )
        resultsproxy = db.execute(statement)
        haadf_path_updated = resultsproxy.rowcount == 1
        updated = updated or haadf_path_updated

    if notes is not None:
        statement = (
            update(models.Scan)
            .where(models.Scan.id == id)
            .where(or_(models.Scan.notes != notes, models.Scan.notes == None))
            .values(notes=notes)
        )
        resultsproxy = db.execute(statement)
        notes_updated = resultsproxy.rowcount == 1
        updated = updated or notes_updated

    db.commit()

    return (updated, get_scan(db, id))


def count(db: Session) -> int:
    return db.query(models.Scan).count()


def delete_scan(db: Session, id: int) -> None:
    db.query(models.Scan).filter(models.Scan.id == id).delete()
    db.commit()


def get_location(db: Session, id: int):
    return db.query(models.Location).filter(models.Location.id == id).first()


def delete_location(db: Session, id: int) -> None:
    db.query(models.Location).filter(models.Location.id == id).delete()
    db.commit()


def delete_locations(db: Session, scan_id: int, host: str) -> None:
    db.query(models.Location).filter(
        models.Location.scan_id == scan_id, models.Location.host == host
    ).delete()
    db.commit()


def get_prev_next_scan(
    db: Session, id: int
) -> Tuple[Union[int, None], Union[int, None]]:
    prev_scan = (
        db.query(models.Scan.id)
        .order_by(models.Scan.id.desc())
        .filter(models.Scan.id < id)
        .limit(1)
        .scalar()
    )
    next_scan = (
        db.query(models.Scan.id)
        .order_by(models.Scan.id.asc())
        .filter(models.Scan.id > id)
        .limit(1)
        .scalar()
    )

    return (prev_scan, next_scan)

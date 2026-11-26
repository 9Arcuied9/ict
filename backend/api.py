from datetime import datetime
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Path, Response
from fastapi.responses import FileResponse, JSONResponse
import psycopg
from pydantic import BaseModel

from web.coords import wgs84_to_gcj02

from .db import connect


app = FastAPI(title='道路病害详情 API', version='1.0')
DefectId = Annotated[int, Path(ge=1, le=2**63 - 1)]
WEB_ROOT = FilePath(__file__).resolve().parents[1] / 'web'


@app.get('/', include_in_schema=False)
def preview():
    return FileResponse(FilePath(__file__).with_name('preview.html'), media_type='text/html')


@app.get('/map/', include_in_schema=False)
def map_page():
    return FileResponse(WEB_ROOT / 'index.html', media_type='text/html')


@app.get('/map/config.js', include_in_schema=False)
def map_config():
    return FileResponse(WEB_ROOT / 'config.js', media_type='text/javascript')


class MapDefect(BaseModel):
    id: str
    type: str
    last_seen: datetime | None
    obs_count: int
    lng_gcj: float | None
    lat_gcj: float | None
    image_url: str | None


class Observation(BaseModel):
    trip_id: str
    event_id: str
    type: str
    captured_at: datetime | None
    lat: float | None
    lon: float | None
    peak_s: float | None
    gps_quality: str | None
    decision: str | None


class DefectDetail(BaseModel):
    defect_id: int
    type: str
    coordinate_system: Literal['WGS84'] = 'WGS84'
    lat: float | None
    lon: float | None
    first_seen: datetime | None
    last_seen: datetime | None
    observation_count: int
    heading: float | None
    road_side: str | None
    image_url: str | None
    observations: list[Observation]


@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(psycopg.errors.UndefinedTable)
def database_unavailable(request, exc):
    return JSONResponse(status_code=503, content={'detail': '病害数据库暂不可用，请检查连接和导入状态'})


@app.get('/api/defects', response_model=list[MapDefect], summary='读取地图和病害列表')
def list_defects():
    with connect() as conn:
        rows = conn.execute("""
            SELECT d.defect_id::text AS id, d.damage_class AS type, d.last_seen,
                   d.observation_count AS obs_count, d.lon, d.lat,
                   CASE WHEN i.defect_id IS NOT NULL THEN '/api/defects/' || d.defect_id || '/image' END AS image_url
            FROM inspection.defects d
            LEFT JOIN inspection.images i ON i.defect_id = d.defect_id AND i.crop = d.crop
            ORDER BY d.defect_id
        """).fetchall()
    for row in rows:
        lng, lat = row.pop('lon'), row.pop('lat')
        row['lng_gcj'], row['lat_gcj'] = wgs84_to_gcj02(lng, lat) if lng is not None else (None, None)
    return rows


@app.get('/api/defects/{defect_id}', response_model=DefectDetail, summary='按病害编号查询档案和观测时间线')
def defect_detail(defect_id: DefectId):
    with connect() as conn:
        # 同一条查询中的档案和时间线来自同一数据库快照。
        row = conn.execute("""
            SELECT d.defect_id, d.damage_class AS type, d.lat, d.lon, d.first_seen, d.last_seen,
                   d.observation_count, d.heading, d.road_side,
                   CASE WHEN i.defect_id IS NOT NULL THEN '/api/defects/' || d.defect_id || '/image' END AS image_url,
                   COALESCE((
                       SELECT jsonb_agg(jsonb_build_object(
                           'trip_id', o.trip_id, 'event_id', o.event_id, 'type', o.damage_class,
                           'captured_at', o.captured_at, 'lat', o.lat, 'lon', o.lon,
                           'peak_s', o.peak_s, 'gps_quality', o.gps_quality, 'decision', o.decision
                       ) ORDER BY o.captured_at NULLS LAST, o.trip_id)
                       FROM inspection.observations o WHERE o.defect_id = d.defect_id
                   ), '[]'::jsonb) AS observations
            FROM inspection.defects d
            LEFT JOIN inspection.images i ON i.defect_id = d.defect_id AND i.crop = d.crop
            WHERE d.defect_id = %s
        """, (defect_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '找不到该病害')
    return row


@app.get('/api/defects/{defect_id}/image', response_class=Response,
         responses={200: {'content': {'image/jpeg': {}}}}, summary='查看该处病害的代表图')
def defect_image(defect_id: DefectId):
    with connect() as conn:
        row = conn.execute("""
            SELECT i.jpeg FROM inspection.images i
            JOIN inspection.defects d ON d.defect_id = i.defect_id AND d.crop = i.crop
            WHERE i.defect_id = %s
        """, (defect_id,)).fetchone()
    if row is None:
        raise HTTPException(404, '该病害没有已入库的代表图')
    return Response(row['jpeg'], media_type='image/jpeg')

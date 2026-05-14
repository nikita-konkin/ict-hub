import faulthandler
faulthandler.enable()
import time
from pathlib import Path
from app.tec_map_pipeline import TecMapConfig, load_tecs_parquet, build_leveled_links, build_frame_summary
from app.tec_map_render import TecMapRenderConfig, build_animation_gif_bytes
cases = [
    ('full_no_basemap', '23:00:00', False),
    ('short_basemap', '01:00:00', True),
]
for label, end_time, basemap in cases:
    print('CASE', label, flush=True)
    params = dict(root=Path('/mnt/tecsuite-parquet-out'), date='2026-01-02', stations=['aksu','alme','arsk','kukm'], start_time='00:00:00', end_time=end_time, min_elevation_deg=20.0)
    pipeline = TecMapConfig(min_elevation_deg=20.0, sampling_interval_seconds=300, frame_minutes=15, ionosphere_height_km=350.0, grid_resolution_deg=1.0, smoothing_sigma=1.0)
    render = TecMapRenderConfig(basemap_enabled=basemap, basemap_cache_root=Path('/app/data/basemap_cache') if basemap else None, frame_dpi=80)
    t0=time.time(); raw=load_tecs_parquet(**params); print(' raw', len(raw), round(time.time()-t0,2), flush=True)
    t0=time.time(); leveled=build_leveled_links(raw, pipeline); print(' leveled', len(leveled), round(time.time()-t0,2), flush=True)
    t0=time.time(); frames=build_frame_summary(leveled, pipeline); print(' frames', len(frames), frames['frame_time'].nunique(), round(time.time()-t0,2), flush=True)
    t0=time.time(); out=build_animation_gif_bytes(frame_summary=frames, pipeline=pipeline, render=render); print(' gif', len(out), round(time.time()-t0,2), flush=True)

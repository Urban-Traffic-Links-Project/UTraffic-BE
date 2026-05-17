import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

from sqlmodel import Session, select
from src.storage.database import engine
from src.storage.models.traffic_dashboard import TrafficMonitoredSegment, TrafficSnapshot

with Session(engine) as session:
    segs = session.exec(select(TrafficMonitoredSegment)).all()
    snaps = session.exec(select(TrafficSnapshot)).all()
    print(f"Monitored segments: {len(segs)}")
    print(f"Traffic snapshots: {len(snaps)}")

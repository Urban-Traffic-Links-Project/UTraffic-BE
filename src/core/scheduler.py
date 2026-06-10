import asyncio

# from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

from datetime import datetime

from src.db.session import create_session
from src.modules.incidents.service import fetch_match_and_save_incidents
from src.modules.traffic_dashboard.service import collect_traffic_snapshots

scheduler = BackgroundScheduler()


def collect_traffic_job():
    print("[TrafficScheduler] Start collecting traffic snapshots")
    with create_session() as session:
        asyncio.run(collect_traffic_snapshots(session))
    print("[TrafficScheduler] Finished collecting traffic snapshots")


def fetch_incidents_job():
    print("[IncidentScheduler] Fetching fresh incidents from TomTom API...")
    with create_session() as session:
        fetch_match_and_save_incidents(session)
    print("[IncidentScheduler] Finished fetching incidents.")


def start_scheduler():
    if scheduler.running:
        return

    # Job 1: Thu thập snapshot tương quan (30 phút/lần)
    scheduler.add_job(
        collect_traffic_job,
        "interval",
        minutes=30,
        id="collect_traffic_snapshots",
        replace_existing=True,
    )

    # Job 2: Lấy sự cố giao thông mới (5 phút/lần)
    scheduler.add_job(
        fetch_incidents_job,
        "interval",
        minutes=5,
        id="fetch_incidents",
        replace_existing=True,
        next_run_time=datetime.now(), # Chạy ngay khi khởi động
    )

    scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
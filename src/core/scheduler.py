import asyncio

# from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

from src.db.session import create_session
from src.modules.traffic_dashboard.service import collect_traffic_snapshots

scheduler = BackgroundScheduler()


def collect_traffic_job():
    print("[TrafficScheduler] Start collecting traffic snapshots")

    with create_session() as session:
        asyncio.run(collect_traffic_snapshots(session))

    print("[TrafficScheduler] Finished collecting traffic snapshots")


def start_scheduler():
    if scheduler.running:
        return

    scheduler.add_job(
        collect_traffic_job,
        "interval",
        minutes=30,
        id="collect_traffic_snapshots",
        replace_existing=True,
        # next_run_time=datetime.now(), - đoạn này để chạy ngay khi khởi động, tạm thời bỏ để tránh đầy bộ nhớ
    )

    scheduler.start()


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
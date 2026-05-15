import asyncio
import sys
import os

sys.path.append(os.path.join(os.getcwd(), "src"))

from src.db.session import create_session
from src.modules.traffic_dashboard.service import seed_monitored_segments_from_nodes, collect_traffic_snapshots

async def run_seed():
    print("🌱 Seeding monitored segments for Dashboard...")
    with create_session() as session:
        created = seed_monitored_segments_from_nodes(session, limit=100)
        print(f"✅ Created {created} monitored segments.")
        
        print("📸 Collecting initial traffic snapshots (this might take a few seconds)...")
        snapshots = await collect_traffic_snapshots(session)
        print(f"✅ Collected {snapshots} snapshots.")

if __name__ == "__main__":
    asyncio.run(run_seed())

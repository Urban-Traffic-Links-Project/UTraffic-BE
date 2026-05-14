"""
Import tất cả models ở đây để SQLModel.metadata biết về chúng.
Quan trọng: file này phải được import TRƯỚC khi gọi create_all() hoặc Alembic.

Thứ tự import có thể quan trọng nếu có circular dependency.
Thứ tự an toàn: graph → auth → traffic → ml → correlation
(graph không phụ thuộc ai, correlation phụ thuộc nhiều nhất)
"""

# Nhóm 1: Road Graph (không phụ thuộc bảng nào khác)
# Nhóm 2: Auth (không phụ thuộc graph)
from src.storage.models.auth import RefreshToken, User, UserSession

# Nhóm 5: Correlation (phụ thuộc ModelVersion, Node)
from src.storage.models.correlation import (
    CorrelationSnapshot,
    NodeCorrelation,
    NodeCorrelationCache,
)
from src.storage.models.graph import (
    Edge,
    GraphSnapshot,
    Node,
    SegmentEdgeMapping,
    TomtomSegment,
)

# Nhóm 4: ML (phụ thuộc GraphSnapshot, Node)
# from src.storage.models.ml import (
#     CongestionLabel,
#     ModelHorizonMetric,
#     ModelVersion,
#     Prediction,
# )

# Nhóm 3: Traffic (phụ thuộc Edge)
from src.storage.models.traffic import HourlyTrafficStat, TrafficObservation

# Nhóm 6: Traffic Dashboard
from src.storage.models.traffic_dashboard import (
    TrafficMonitoredSegment,
    TrafficSnapshot,
)

__all__ = [
    # Graph
    "GraphSnapshot",
    "Node",
    "Edge",
    "TomtomSegment",
    "SegmentEdgeMapping",
    # Auth
    "User",
    "RefreshToken",
    "UserSession",
    # Traffic
    "TrafficObservation",
    "HourlyTrafficStat",
    # ML
    # "ModelVersion",
    # "ModelHorizonMetric",
    # "Prediction",
    # "CongestionLabel",
    # Correlation
    "CorrelationSnapshot",
    "NodeCorrelation",
    "NodeCorrelationCache",
    # Traffic Dashboard
    "TrafficMonitoredSegment",
    "TrafficSnapshot",
]

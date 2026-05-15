"""
Import tất cả models ở đây để SQLModel.metadata biết về chúng.
Quan trọng: file này phải được import TRƯỚC khi gọi create_all() hoặc Alembic.

Thứ tự import có thể quan trọng nếu có circular dependency.
Thứ tự an toàn: graph → auth → traffic → ml → correlation → incident
(graph không phụ thuộc ai, correlation phụ thuộc nhiều nhất)
"""

# Nhóm 1: Road Graph (không phụ thuộc bảng nào khác)
from src.storage.models.graph import (
    Edge,
    GraphSnapshot,
    Node,
    SegmentEdgeMapping,
    TomtomSegment,
)

# Nhóm 2: Auth (không phụ thuộc graph)
from src.storage.models.auth import RefreshToken, User, UserSession

# Nhóm 3: Traffic (phụ thuộc Edge)
from src.storage.models.traffic import HourlyTrafficStat, TrafficObservation

# Nhóm 4: ML (phụ thuộc GraphSnapshot, Node)
from src.storage.models.ml import (
    CongestionLabel,
    ModelHorizonMetric,
    ModelVersion,
    Prediction,
)

# Nhóm 5: Correlation (phụ thuộc ModelVersion, Node)
from src.storage.models.correlation import (
    CorrelationSnapshot,
    EdgeCorrelation,
    NodeCorrelation,
    NodeCorrelationCache,
)

# Nhóm 6: Incidents (phụ thuộc Edge)
from src.storage.models.incident import Incident, IncidentEdge

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
    "ModelVersion",
    "ModelHorizonMetric",
    "Prediction",
    "CongestionLabel",
    # Correlation
    "CorrelationSnapshot",
    "NodeCorrelation",
    "NodeCorrelationCache",
    "EdgeCorrelation",
    # Incidents
    "Incident",
    "IncidentEdge",
    # Traffic Dashboard
    "TrafficMonitoredSegment",
    "TrafficSnapshot",
]

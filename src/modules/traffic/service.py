"""src/modules/traffic/service.py"""
from sqlmodel import Session, select
from src.storage.models.graph import Node, Edge


def get_all_nodes(session: Session) -> list[Node]:
    return session.exec(select(Node).order_by(Node.node_index)).all()


def get_all_edges(session: Session) -> list[dict]:
    """Trả về edges kèm tọa độ source/target để vẽ Polyline."""
    edges = session.exec(select(Edge)).all()
    nodes = {n.id: n for n in session.exec(select(Node)).all()}

    result = []
    for e in edges:
        src = nodes.get(e.source_node_id)
        tgt = nodes.get(e.target_node_id)
        if src and tgt:
            result.append({
                "edge_id": e.id,
                "source_osm_id": src.osm_node_id,
                "target_osm_id": tgt.osm_node_id,
                "source_lat": src.lat,
                "source_lon": src.lon,
                "target_lat": tgt.lat,
                "target_lon": tgt.lon,
                "length_m": e.length_m,
            })
    return result
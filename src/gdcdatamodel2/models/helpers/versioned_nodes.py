from copy import copy
from typing import Any, TypeVar

from psqlgraph import Node
from sqlalchemy import BigInteger, Column, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.declarative import declarative_base

Base: Any = declarative_base()

T = TypeVar("T", bound="VersionedNode")


class VersionedNode(Base):
    __tablename__ = "versioned_nodes"
    __table_args__ = (
        Index("submitted_node_id_idx", "node_id"),
        Index("submitted_node_gdc_versions_idx", "node_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<VersionedNode(key={self.key}, label='{self.label}', node_id='{self.node_id}')>"
        )

    key = Column(BigInteger, primary_key=True, nullable=False)

    label = Column(
        Text,
        nullable=False,
    )

    node_id = Column(
        Text,
        nullable=False,
    )

    project_id = Column(
        Text,
        nullable=False,
    )

    gdc_versions = Column(
        ARRAY(Text),
    )

    created = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    versioned = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    acl = Column(
        ARRAY(Text),
        default=list(),
    )

    system_annotations = Column(
        JSONB,
        default={},
    )

    properties = Column(
        JSONB,
        default={},
    )

    neighbors = Column(
        ARRAY(Text),
    )

    @classmethod
    def clone(cls: type[T], node: Node) -> T:
        return cls(
            label=copy(node.label),
            node_id=copy(node.node_id),
            project_id=copy(node._props.get("project_id")),
            created=copy(node.created),
            acl=copy(node.acl),
            system_annotations=copy(node.system_annotations),
            properties=copy(node.properties),
            neighbors=copy(
                [edge.dst_id for edge in node.edges_out]
                + [edge.src_id for edge in node.edges_in]
            ),
        )
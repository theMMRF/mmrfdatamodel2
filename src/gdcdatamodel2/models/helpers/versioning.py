"""Creat Tags and versions for biograph.

Biogrpah will save different version for a node. And usually the latest version for a
gencode version will be exported and imported into gdcgraph.

To accomplish this, we use tagging and versioning to find different versions of nodes.

Rules:
1. The nodes with same parents and tag properties will have the same tag.
2. The nodes with dame tag will have different version number according to creation time.
3. The newest created node will have the latest set to True, and all other versions will
    have it set to False.
4. ver, tag, latest will be set at node creation with hooks.
"""

import os
import uuid
from collections.abc import Iterator
from functools import cache
from typing import Any

import psqlgraph
import sqlalchemy
from sqlalchemy import engine, event, orm

UUID_NAMESPACE_SEED = os.getenv("UUID_NAMESPACE_SEED", "86bb916a-24c5-48e4-8a46-5ea73a379d47")
UUID_NAMESPACE = uuid.UUID(f"urn:uuid:{UUID_NAMESPACE_SEED}", version=4)


class TagKeys:
    tag = "tag"
    latest = "latest"
    version = "ver"


class TaggingConstraint:
    """Computes whether a node instance supports tagging or not."""

    def __init__(self, path: str, prop: str, values: list[str]) -> None:
        """Initialize TaggingConstraint.

        Args:
            path (str): full psqlgraph path to a parent node
            prop (str): valid node property name
            values (list[str]): list of possible value
        """
        self.path = path
        self.prop = prop
        self.values = values

    def _resolve_target_node_from_path(self, node: psqlgraph.Node) -> psqlgraph.Node:
        """Resolve to the final node instance that can be used to perform the matching.

        e.g: if path = `aligned_reads.submitted_alinged_reads`, the final node used to
        perform the matching is an instance of SubmittedAlignedReads, which can be reached
        by following the relationships defined in the path
            i.e: node["aligned_reads"][0]["submitted_aligned_reads"][0]
            this is equivalent to:
                node.aligned_reads[0].submitted_aligned_reads[0]

        Args:
            node (models.Node): Node instance

        Returns:
            models.Node: node instance whose properties will be used for matching
        """
        if not self.path:
            return node

        for path in self.path.split("."):
            # Since a node type can have multiple paths to a given parent
            # this check allows instances that do not have this specific path
            if len(node[path]) == 0:
                return None

            node = node[path][0]
        return node

    def match(self, node: psqlgraph.Node) -> bool:
        """Check if a node has a value matching the prop and values field.

        if it does, the particular instance will not participate in the entire tagging process

        Args:
            node (psqlgraph.Node): node instance

        Returns:
            Returns (bool)
        """
        node = self._resolve_target_node_from_path(node)
        return node and node[self.prop] in self.values


class TagBuilderConfig:
    """A wrapper around the tagBuilderConfig definition in the dictionary yaml."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg

    def _constraints(self) -> Iterator[TaggingConstraint]:
        """Return all constraints defined for a particular node type."""
        skip_criterion = self.cfg.get("ignoreEntries", [])
        for criteria in skip_criterion:
            yield TaggingConstraint(
                path=criteria.get("path"),
                prop=criteria["prop"],
                values=criteria["values"],
            )

    def is_taggable(self, node: psqlgraph.Node) -> bool:
        """Return true if node supports tagging else False.

            Ideally, instances that return false will not
            have tag and version number set on them

        Returns:
            bool: True for nodes that can be tagged
        """
        return not any(criteria.match(node) for criteria in self._constraints())


def __generate_hash(seed: list[str], label: str) -> str:
    namespace = UUID_NAMESPACE
    name = f"{seed}-{label}"
    return str(uuid.uuid5(namespace, name))


@cache
def compute_tag(node: psqlgraph.Node) -> str:
    """Compute unique tag for given node.

    Args:
        node (models.Node): mode instance
    Returns:
        str: computed tag
    """
    keys = node.get_tag_property_values()
    keys += sorted(
        compute_tag(p.dst)
        for p in node.edges_out
        if p.dst.is_taggable() and p.label != "relates_to"
    )
    return __generate_hash(keys, node.label)


def __get_tagged_version(
    node_id: str, table: sqlalchemy.Table, tag: str, conn: sqlalchemy.engine.Connection
) -> int:
    """Private function to determine the version number to use just after insertion.

    Args:
        node_id (str): current node_id
        table (sqlalchemy.Table): node table instance
        tag (str): currently computed tag
        conn (sqlalchemy.engine.Connection): currently active connection instance

    Returns:
        int: appropriate version number to use. 1 greater than the current max
    """
    query = sqlalchemy.select([table]).where(
        sqlalchemy.and_(table.c._sysan[TagKeys.tag].astext == tag, table.c.node_id != node_id)
    )
    max_version = 0
    for r in conn.execute(query):
        max_version = max(r._sysan.get(TagKeys.version, 0), max_version)

        # reset latest
        r._sysan[TagKeys.latest] = False
        conn.execute(
            table.update().where(table.c.node_id == r.node_id).values(_sysan=r._sysan)
        )
    return max_version + 1


def inject_set_tag_after_insert(cls: psqlgraph.Node) -> None:
    """Add hook to set tag, and version info for node after insert.

    Inject an event listener that sets the tag and version properties on nodes,
    just before they are inserted

    Args:
        cls (class): node class type
    """

    @event.listens_for(cls, "after_insert")
    def set_node_tag(
        mapper: orm.Mapper, conn: engine.base.Connection, node: psqlgraph.Node
    ) -> None:
        table = node.__table__

        if not node.is_taggable():
            return  # do nothing

        tag = compute_tag(node)

        version = __get_tagged_version(node.node_id, table, tag, conn)

        node._sysan[TagKeys.tag] = tag
        node._sysan[TagKeys.latest] = True
        node._sysan[TagKeys.version] = version

        # update tag and version
        conn.execute(
            table.update().where(table.c.node_id == node.node_id).values(_sysan=node._sysan)
        )
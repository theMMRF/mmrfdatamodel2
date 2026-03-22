"""This file only contains helper functions about related cases."""

import itertools
from collections.abc import Collection, Generator, Iterator

import psqlgraph
from sqlalchemy import orm
from sqlalchemy.orm import unitofwork

#: This variable contains the link name for the case shortcut
#: association proxy
RELATED_CASES_LINK_NAME = "_related_cases"


def get_edge_src(edge: psqlgraph.Edge) -> psqlgraph.Node | None:
    """Look up edge source node by id if the association proxy is not set.

    Args:
        edge: edge whose source node we are looking for

    Returns:
        Return the edge's source or None.
    """
    node_cls = edge.get_node_class()

    if edge.src:
        src = edge.src
    elif edge.src_id is not None:
        src_class = node_cls.get_subclass_named(edge.__src_class__)
        src = (
            edge.get_session()
            .query(src_class)
            .filter(src_class.node_id == edge.src_id)
            .first()
        )
    else:
        src = None
    return src


def get_edge_dst(edge: psqlgraph.Edge, allow_query: bool = False) -> psqlgraph.Node | None:
    """Look up edge destination node.

    Args:
        edge: edge whose destination node we are looking for
        allow_query: whether we can query db by node id

    Returns:
        Return the edge's destination or None.
    """
    node_cls = edge.get_node_class()

    if edge.dst:
        dst = edge.dst
    elif edge.dst_id is not None and allow_query:
        dst_class = node_cls.get_subclass_named(edge.__dst_class__)
        dst = (
            edge.get_session()
            .query(dst_class)
            .filter(dst_class.node_id == edge.dst_id)
            .first()
        )
    else:
        dst = None

    return dst


def get_related_cases_from_cache(
    node: psqlgraph.Node,
) -> Generator[psqlgraph.Node, None, None]:
    """Get the cached related case ids from this node's case shortcut edges.

    Args:
        node: The Node instance

    Yields:
        case node
    """
    for case in getattr(node, RELATED_CASES_LINK_NAME, []):
        if case is not None:
            yield case


def get_related_case_edge_cls_name(node: psqlgraph.Node) -> str:
    """Generate shortcut edge class name.

    Args:
        node: The source node of the edge

    Returns:
        the related case edge class name
    """
    return f"{node.__class__.__name__}RelatesToCase"


def get_related_cases_from_parents(node: psqlgraph.Node) -> Iterator[psqlgraph.Node]:
    """Get the cached related case ids from the parents of this node.

    Get the cached related case ids from the parents of this node from
    1. The shortcut edges of any parent nodes
    2. If any parents are cases, include those ids

    Args:
        node: The Node instance

    Returns:
        iterator of case nodes
    """
    skip_edges_named = [get_related_case_edge_cls_name(node)]

    # Make sure the edges haven't been expunged
    edges_out = [e for e in node.edges_out if e in node.get_session()]

    # Get the cached ids from parents
    edges_out_filtered = (e for e in edges_out if e.__class__.__name__ not in skip_edges_named)
    dsts = (e.dst for e in edges_out_filtered if e.dst)
    cases_chain = itertools.chain.from_iterable(dst._related_cases_from_cache for dst in dsts)
    cases = set(cases_chain)

    # Are any parents cases?
    for edge in edges_out:
        if edge.__class__.__name__ in skip_edges_named:
            continue
        node_cls = edge.get_node_class()
        dst_class = node_cls.get_subclass_named(edge.__dst_class__)
        if dst_class.label == "case" and edge.dst:
            cases.add(edge.dst)

    return filter(None, cases)


def update_cache_edges(node: psqlgraph.Node, correct_cases: dict[str, psqlgraph.Node]) -> None:
    """Create new edges or deletes old edges.

        Given node and a dictionary of correct_cases
        When update_cache_edges called
        Then remove cases not in correct_cases from node's _related_cases
        And add missing edges from correct_cases to node's _related_cases

    Args:
        node: node to update _related_cases
        correct_cases: a dictionary of correct_cases
    """
    assoc_proxy = getattr(node, RELATED_CASES_LINK_NAME)

    # Get information about the existing edges
    edge_name = get_related_case_edge_cls_name(node)
    existing_edges = getattr(node, f"_{edge_name}_out")

    existing_edge_dst_case_dict = {e.dst_id: e.dst for e in existing_edges}

    case_ids_disconnected = set(existing_edge_dst_case_dict) - set(correct_cases)
    case_ids_connected = set(correct_cases) - set(existing_edge_dst_case_dict)

    for case_id in case_ids_disconnected:
        assoc_proxy.remove(existing_edge_dst_case_dict[case_id])

    assoc_proxy.extend([correct_cases[case_id] for case_id in case_ids_connected])


def cache_related_cases_recursive(
    node: psqlgraph.Node, visited_nodes: set[str] | None = None
) -> None:
    """Update the related case cache on source node and its children recursively.

        Update the related case cache on source node and its children
        recursively iff the update changes the related case source
        node's shortcut edges.

    Args:
        node: root node to update
        visited_nodes: remember visited node for dfs
    """
    visited_nodes = set() if visited_nodes is None else visited_nodes

    # Check preconditions for updating shortcut edge
    if not node or not hasattr(node, RELATED_CASES_LINK_NAME):
        return

    if node.node_id in visited_nodes:
        return

    visited_nodes.add(node.node_id)

    # These are the cases that are currently connected by a shortcut edge
    current_cases = {c.node_id: c for c in get_related_cases_from_cache(node)}

    # These are the cases are currently connected by a shortcut edge
    # to this node's parents
    updated_cases = {c.node_id: c for c in get_related_cases_from_parents(node)}

    diff = current_cases.keys() ^ updated_cases.keys()

    # If nothing has changed, we don't need to update or recur
    if not diff:
        return

    update_cache_edges(node, updated_cases)

    to_recur = (e for e in node.edges_in if e.src)
    for edge in to_recur:
        cache_related_cases_recursive(
            get_edge_src(edge),
            visited_nodes,
        )


def cache_related_cases_on_insert(
    target: psqlgraph.Edge,
    session: orm.Session,
    flush_context: unitofwork.UOWTransaction,
    instances: Collection[psqlgraph.Node | psqlgraph.Edge] | None,
) -> None:
    """Update the related case cache on source node and its children for edge insertion.

        Hook on created edges.  Update the related case cache on source
        node and its children iff this update changes the related case
        source node's cache.
        This will be called when an edge is inserted to db.

        https://docs.sqlalchemy.org/en/14/orm/events.html#sqlalchemy.orm.SessionEvents.before_flush

    Args:
        target: psqlgraph edge to insert
        session: sqlalchemy session of the target
        flush_context: Internal UOWTransaction object which handles the details of
            the flush.
        instances:  Usually None, this is the collection of objects which can be
            passed to the Session.flush() method (note this usage is
            deprecated).
    """
    if not target.src:
        target.src = get_edge_src(target)

    if not target.dst:
        target.dst = get_edge_dst(target, allow_query=True)

    cache_related_cases_recursive(get_edge_src(target))


def cache_related_cases_on_update(
    target: psqlgraph.Edge,
    session: orm.Session,
    flush_context: unitofwork.UOWTransaction,
    instances: Collection[psqlgraph.Node | psqlgraph.Edge] | None,
) -> None:
    """Update the related case cache on source node and its children for edge update.

        Hook on updated edges.  Update the related case cache on source
        node and its children iff this update changes the related case
        source node's cache.
        This will be called when an edge instance is updated.

        https://docs.sqlalchemy.org/en/14/orm/events.html#sqlalchemy.orm.SessionEvents.before_flush

    Args:
        target: psqlgraph edge to insert
        session: sqlalchemy session of the target
        flush_context: Internal UOWTransaction object which handles the details of
            the flush.
        instances:  Usually None, this is the collection of objects which can be
            passed to the Session.flush() method (note this usage is
            deprecated).
    """
    cache_related_cases_recursive(get_edge_src(target))


def cache_related_cases_on_delete(
    target: psqlgraph.Edge,
    session: orm.Session,
    flush_context: unitofwork.UOWTransaction,
    instances: Collection[psqlgraph.Node | psqlgraph.Edge] | None,
) -> None:
    """Update the related case cache on source node and its children for edge deletion.

        Hook on updated edges.  Update the related case cache on source
        node and its children.
        This will be called when an edge instance is explicitly deleted in the session.

        https://docs.sqlalchemy.org/en/14/orm/events.html#sqlalchemy.orm.SessionEvents.before_flush

    Args:
        target: psqlgraph edge to insert
        session: sqlalchemy session of the target
        flush_context: Internal UOWTransaction object which handles the details of
            the flush.
        instances:  Usually None, this is the collection of objects which can be
            passed to the Session.flush() method (note this usage is
            deprecated).
    """
    # Remove the source and destination of application local
    # association_proxy so cache_related_cases_update_children doesn't
    # traverse the edge
    target.dst, target.src = None, None
    cache_related_cases_recursive(get_edge_src(target))
import psqlgraph
from sqlalchemy import engine, event, orm


def cls_inject_created_datetime_hook(
    cls: psqlgraph.Node,
    updated_key: str = "updated_datetime",
    created_key: str = "created_datetime",
) -> None:
    """Add hook to set created and updated datetime for creation.

        Given a psqlgraph node with field names for datetime
        When run cls_inject_created_datetime_hook
        Then a sqlalchemy hook(event listener) will be added to the node
        And datetime will be added to those fields when new node saved

    Args:
        cls: the node to add hook
        updated_key: field name for updated_datetime
        created_key: field name for created_datetime

    Returns:
        None
    """

    @event.listens_for(cls, "before_insert")
    def set_created_updated_datetimes(
        mapper: orm.Mapper, connection: engine.base.Connection, target: psqlgraph.Node
    ) -> None:
        ts = target.get_session()._flush_timestamp.isoformat("T")
        if updated_key in target.props:
            target._props[updated_key] = ts
        if created_key in target.props:
            target._props[created_key] = ts


def cls_inject_updated_datetime_hook(
    cls: psqlgraph.Node, updated_key: str = "updated_datetime"
) -> None:
    """Add hook to set updated datetime for update.

        Given a psqlgraph node with field name to update
        When run cls_inject_updated_datetime_hook
        Then a sqlalchemy hook(event listener) will be added to the node
        And the datetime will be updated for the node when node changed.

    Args:
        cls: the node to add hook
        updated_key: field name for updated_datetime

    Returns:
        None
    """

    @event.listens_for(cls, "before_update")
    def set_updated_datetimes(
        mapper: orm.Mapper, connection: engine.base.Connection, target: psqlgraph.Node
    ) -> None:
        # SQLAlchemy fires this event when associations change, but we should
        # only adjust the timestamp if the object itself was modified.
        target_session = target.get_session()
        if target_session.is_modified(target, include_collections=False):
            ts = target_session._flush_timestamp.isoformat("T")
            if updated_key in target.props:
                target._props[updated_key] = ts
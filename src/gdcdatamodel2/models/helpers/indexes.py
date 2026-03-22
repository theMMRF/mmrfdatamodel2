"""This file is for __pg_secondary_keys of node.

The secondary keys used for querying a node:

    secondary_keys = (("project_1", "submitter_1"),)

    case = (
        graph_driver.nodes(Case).filter(Case._secondary_keys == secondary_keys).one_or_none()
    )

To speed up the query, indexes are built based on secondary keys.

    "index_node_case_submitter_id",
    "index_node_case_project_id",
    "index_node_case_project_id_lower",
    "index_node_case_submitter_id_lower",
"""

import hashlib
import logging
from collections.abc import Iterable
from typing import Any

import psqlgraph
import sqlalchemy
from sqlalchemy import Index, func
from sqlalchemy.ext import hybrid

logger = logging.getLogger(__name__)


def index_name(cls: type[psqlgraph.Node], description: str) -> str:
    """Standardize index naming.

        Because of PostgreSQL's name character
        limit, this follows a similar scheme to shortening edge names in
        `gdcdatamodel.generate_edge_tablename()`
        For long names, take the first 8 characters of a hash of the full,
        un-truncated table name *before* we truncate and prepend this to
        the truncation.  This gets us a name like
        ``index_4df72441_famihist_lower_submitte_id``.  This is yet
        another rather an undesirable workaround. - jsm

    Args:
        cls: node class to build index with
        description: usually the field name to index

    Returns:
        index name
    """
    name = f"index_{cls.__tablename__}_{description}"

    # If the name is too long, prepend it with the first 8 hex of it's hash
    # truncate the each part of the name
    if len(name) > 40:
        old_name = name
        logger.debug(f"index name '{old_name}' too long, shortening")

        __name = cls.__tablename__.encode("utf-8")
        # hash is not used for security, only used to generate unique names,
        # and all data used are internally generated.
        md5_hash = hashlib.md5(__name, usedforsecurity=False)

        short_md5 = md5_hash.hexdigest()[:8]
        short_label = "".join([a[:4] for a in cls.get_label().split("_")])[:20]
        short_description = "_".join([a[:8] for a in description.split("_")])[:25]

        name = f"index_{short_md5}_{short_label}_{short_description}"

        logger.debug(f"Shortening {old_name} -> {name}")

    return name


def get_secondary_key_indexes(cls: type[psqlgraph.Node]) -> tuple:
    """Get tuple of indexes on the secondary keys of the class.

    Args:
        cls: Class to create indexes

    Returns:
        Tuple of indexes
    """
    #: use text_pattern_ops, allows LIKE statements not starting with %
    index_op = "text_pattern_ops"
    secondary_keys = {key for keys in cls.pg_secondary_keys() for key in keys}

    key_indexes = (
        Index(
            index_name(cls, key),
            cls._props[key].astext.label(key),
            postgresql_ops={key: index_op},
        )
        for key in secondary_keys
    )

    lower_key_indexes = (
        Index(
            index_name(cls, key + "_lower"),
            func.lower(cls._props[key].astext).label(key + "_lower"),
            postgresql_ops={key + "_lower": index_op},
        )
        for key in secondary_keys
    )

    return tuple(key_indexes) + tuple(lower_key_indexes)


class SecondaryKeyComparator(hybrid.Comparator):
    def __eq__(self, other: Iterable[Iterable[Any]]) -> bool:  # type: ignore
        """Check whether node(self) contains the _secondary_key values in `other`.

            Compare the node (self) and check whether it contains the _secondary_key
            values found in other. Other contains a iterable of iterables where each
            sub iterable contains a sequence of values which corresponds to the node's
            _secondary_keys

            Given an Iterable of secondary keys and a node
            When checking whether the secondary keys are equal
            Then create dictionaries containing secondary keys and values from other
            And return True if the nodes have same values for those keys

            NOTE: It is possible to pass only a subset of the secondary keys as long as
            they maintain the sequential  order of the node's _secondary_keys. Also any
            extra key values passed in other will be ignored and not affect the
            comparison. Finally, an empty set of values e.g. ((),) will match all
            objects in the data store.

        Args:
            other: the values of the _secondary_keys being tested. (Note: The field
                _secondary_keys contains the values of uniqueProperties
                instead of the keys.)

        Returns:
            Ture iff the value for uniqueKeys are the same.
        """
        filters = []
        cls = self.__clause_element__()
        secondary_keys = cls.pg_secondary_keys()
        key_pairs = (
            (keys, values) for keys, values in zip(secondary_keys, other) if "id" not in keys
        )
        for keys, values in key_pairs:
            other_dict = {key: val for key, val in zip(keys, values)}
            filters.append(cls._props.contains(other_dict))
        return sqlalchemy.and_(*filters)
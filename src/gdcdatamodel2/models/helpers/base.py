import os

from psqlgraph import ext

# namespace needed for multiple dictionaries to work at same time(graphmanager)
namespace = os.getenv("GDCDICTIONARY_NAMESPACE") or None
# the register_base_class is needed for edge.get_node_class to work
Node, Edge = ext.register_base_class(namespace)
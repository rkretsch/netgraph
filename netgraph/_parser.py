#!/usr/bin/env python
"""
Functions for reading and writing graphs.
"""
import numpy as np
import warnings

from functools import wraps

from ._utils import _save_cast_float_to_int, _get_unique_nodes


def _handle_multigraphs(parser):
    def wrapped_parser(graph, *args, **kwargs):
        nodes, edges, edge_weight = parser(graph, *args, **kwargs)

        if len(set(edges)) < len(edges):
            msg = "Multi-graphs are not properly supported. Removing duplicate edges before plotting."
            warnings.warn(msg)
            new_edges = []
            for edge in edges:
                if edge not in new_edges:
                    new_edges.append(edge)
            return nodes, new_edges, edge_weight

        return nodes, edges, edge_weight

    return wrapped_parser


def parse_graph(graph):
    """
    Arguments
    ----------
    graph: various formats
        Graph object to plot. Various input formats are supported.
        In order of precedence:
        - Edge list:
            Iterable of (source, target) or (source, target, weight) tuples,
            or equivalent (E, 2) or (E, 3) ndarray (where E is the number of edges).
        - Adjacency matrix:
            Full-rank (V, V) ndarray (where V is the number of nodes/vertices).
            The absence of a connection is indicated by a zero.
            Note that V > 3 as (2, 2) and (3, 3) matrices will be interpreted as edge lists.
        - networkx.Graph or igraph.Graph object

    Returns:
    --------
    nodes : V-long list of node uids
        List of unique nodes.

    edges: E-long list of 2-tuples
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    edge_weight: dict (source, target) : float or None
        Dictionary mapping edges to weights. If the graph is unweighted, None is returned.

    """

    if isinstance(graph, (list, tuple, set)):
        return _parse_sparse_matrix_format(graph)

    elif isinstance(graph, np.ndarray):
        rows, columns = graph.shape
        if columns in (2, 3):
            return _parse_sparse_matrix_format(graph)
        elif rows == columns:
            return _parse_adjacency_matrix(graph)
        else:
            msg = "Could not interpret input graph."
            msg += "\nIf a graph is specified as a numpy array, it has to have one of the following shapes:"
            msg += "\n\t-(E, 2) or (E, 3), where E is the number of edges"
            msg += "\n\t-(V, V), where V is the number of nodes (i.e. full rank)"
            msg += f"However, the given graph had shape {graph.shape}."

    # this is a terrible way to test for the type but we don't want to import
    # igraph unless we already know that it is available
    elif str(graph.__class__) == "<class 'igraph.Graph'>":
        return _parse_igraph_graph(graph)

    # ditto
    elif str(graph.__class__) in ("<class 'networkx.classes.graph.Graph'>",
                                  "<class 'networkx.classes.digraph.DiGraph'>",
                                  "<class 'networkx.classes.multigraph.MultiGraph'>",
                                  "<class 'networkx.classes.multidigraph.MultiDiGraph'>"):
        return _parse_networkx_graph(graph)

    else:
        allowed = ['list', 'tuple', 'set', 'networkx.Graph', 'igraph.Graph']
        raise NotImplementedError("Input graph must be one of: {}\nCurrently, type(graph) = {}".format("\n\n\t" + "\n\t".join(allowed), type(graph)))


@_handle_multigraphs
def _parse_sparse_matrix_format(adjacency):
    adjacency = np.array(adjacency)
    rows, columns = adjacency.shape

    if columns == 2:
        edges = _parse_edge_list(adjacency)
        nodes = _get_unique_nodes(edges)
        return nodes, edges, None

    elif columns == 3:
        edges = _parse_edge_list(adjacency[:, :2])
        nodes = _get_unique_nodes(edges)
        edge_weight = {(source, target) : weight for (source, target, weight) in adjacency}

        # In a sparse adjacency format with integer nodes and float weights,
        # the type of nodes is promoted to the same type as weights.
        # If all nodes can safely be demoted to ints, then we probably want to do that.
        save = True
        for node in nodes:
            if not isinstance(_save_cast_float_to_int(node), int):
                save = False
                break
        if save:
            nodes = [_save_cast_float_to_int(node) for node in nodes]
            edges = [(_save_cast_float_to_int(source), _save_cast_float_to_int(target)) for (source, target) in edges]
            edge_weight = {(_save_cast_float_to_int(source), _save_cast_float_to_int(target)) : weight for (source, target), weight in edge_weight.items()}

        if len(set(edge_weight.values())) > 1:
            return nodes, edges, edge_weight
        else:
            return nodes, edges, None

    else:
        msg = "Graph specification in sparse matrix format needs to consist of an iterable of tuples of length 2 or 3."
        msg += "Got iterable of tuples of length {}.".format(columns)
        raise ValueError(msg)


def _parse_edge_list(edges):
    # Edge list may be an array, or a list of lists. We want a list of tuples.
    return [(source, target) for (source, target) in edges]


def _parse_adjacency_matrix(adjacency):
    sources, targets = np.where(adjacency)
    edges = list(zip(sources.tolist(), targets.tolist()))
    nodes = list(range(adjacency.shape[0]))
    edge_weights = {(source, target): adjacency[source, target] for (source, target) in edges}

    if len(set(list(edge_weights.values()))) == 1:
        return nodes, edges, None
    else:
        return nodes, edges, edge_weights


@_handle_multigraphs
def _parse_networkx_graph(graph, attribute_name='weight'):
    edges = list(graph.edges)
    nodes = list(graph.nodes)
    try:
        edge_weights = {edge : graph.get_edge_data(*edge)[attribute_name] for edge in edges}
    except KeyError: # no weights
        edge_weights = None
    return nodes, edges, edge_weights


@_handle_multigraphs
def _parse_igraph_graph(graph):
    edges = [(edge.source, edge.target) for edge in graph.es()]
    nodes = graph.vs.indices
    if graph.is_weighted():
        edge_weights = {(edge.source, edge.target) : edge['weight'] for edge in graph.es()}
    else:
        edge_weights = None
    return nodes, edges, edge_weights


def _is_directed(edges):
    # test for bi-directional edges
    for (source, target) in edges:
        if ((target, source) in edges) and (source != target):
            return True
    return False

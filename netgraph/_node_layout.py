#!/usr/bin/env python
"""
TODO:
- suppress warnings for divide by zero on diagonal -- masked arrays?
- ensure that the adjacency matrix has the correct dimensions even is one of the nodes is unconnected
"""

import warnings
import itertools
import numpy as np

from functools import wraps
from scipy.spatial import Voronoi
from rpack import pack
from itertools import combinations

from grandalf.graphs import Vertex, Edge, Graph
from grandalf.layouts import SugiyamaLayout

from ._utils import (
    _edge_list_to_adjacency_matrix,
    _edge_list_to_adjacency_list,
    _get_subgraph,
    _get_unique_nodes,
    _get_n_points_on_a_circle,
    _get_subgraph,
    _invert_dict,
    _get_connected_components,
)


DEBUG = False


def _handle_multiple_components(layout_function):
    """
    Most layout algorithms only handle graphs that consist of a giant
    single component, and fail to find a suitable layout if the graph
    consists of more than component. This decorator wraps a given
    layout function such that if the graph contains more than one
    component, the layout is first applied to each individual
    component, and then the component layouts are combined using
    rectangle packing.

    """
    @wraps(layout_function)
    def wrapped_layout_function(edges, nodes=None, *args, **kwargs):

        # determine if there are more than one component
        adjacency_list = _edge_list_to_adjacency_list(edges, directed=False)
        components = _get_connected_components(adjacency_list)

        if nodes:
            unconnected_nodes = set(nodes) - set(_get_unique_nodes(edges))
            if unconnected_nodes:
                for node in unconnected_nodes:
                    components.append([node])

        if len(components) > 1:
            return get_layout_for_multiple_components(edges, components, layout_function, *args, **kwargs)
        else:
            return layout_function(edges, *args, **kwargs)

    return wrapped_layout_function


def get_layout_for_multiple_components(edges, components, layout_function,
                                       origin = (0, 0),
                                       scale  = (1, 1),
                                       *args, **kwargs):
    """
    Determine suitable bounding box dimensions and placement for each
    component in the graph, and then compute the layout of each
    individual component given the constraint of the bounding box.

    Arguments:
    ----------
    edges : list of (source node, target node) tuples
        The graph to plot.
    components : list of sets of node IDs
        The connected components of the graph.
    layout_function : function handle
        Handle to the function computing the relative positions of each node within a component.
        The args and kwargs are passed through to this function.
    origin : (float x, float y) 2-tuple (default (0, 0))
        Bottom left corner of the frame / canvas containing the graph.
    scale : (float x, float y) 2-tuple (default (1, 1))
        Width, height of the frame.

    Returns:
    --------
    node_positions : dict node : (float x, float y)
        The position of all nodes in the graph.

    """

    bboxes = _get_component_bboxes(components, origin, scale)

    node_positions = dict()
    for ii, (component, bbox) in enumerate(zip(components, bboxes)):
        if len(component) > 1:
            subgraph = _get_subgraph(edges, component)
            component_node_positions = layout_function(subgraph, origin=bbox[:2], scale=bbox[2:], *args, **kwargs)
            node_positions.update(component_node_positions)
        else:
            # component is a single node, which we can simply place at the centre of the bounding box
            node_positions[component.pop()] = (bbox[0] + 0.5 * bbox[2], bbox[1] + 0.5 * bbox[3])

    return node_positions


def _get_component_bboxes(components, origin, scale, power=0.8, pad_by=0.05):
    """
    Partition the canvas given by origin and scale into bounding boxes, one for each component.

    Arguments:
    ----------
    components : list of sets of node IDs
        The unconnected components of the graph.
    origin : D-tuple or None (default None, which implies (0, 0))
        Bottom left corner of the frame / canvas containing the graph.
        If None, it defaults to (0, 0) or the minimum of `node_positions`
        (whichever is smaller).
    scale : D-tuple or None (default None, which implies (1, 1)).
        Width, height, etc of the frame. If None, it defaults to (1, 1) or the
        maximum distance of nodes in `node_positions` to the `origin`
        (whichever is greater).
    power : float (default 0.8)
        The dimensions each bounding box are given by |V|^power by |V|^power,
        where |V| are the total number of nodes.

    Returns:
    --------
    bboxes : list of (min x, min y, width height) tuples
        The bounding box for each component.
    """

    relative_dimensions = [_get_bbox_dimensions(len(component), power=power) for component in components]

    # Add a padding between boxes, such that nodes cannot end up touching in the final layout.
    # We choose a padding proportional to the dimensions of the largest box.
    maximum_dimensions = np.max(relative_dimensions, axis=0)
    pad_x, pad_y = pad_by * maximum_dimensions
    padded_dimensions = [(width + pad_x, height + pad_y) for (width, height) in relative_dimensions]

    # rpack only works on integers, hence multiply by some large scalar to retain some precision;
    # NB: for some strange reason, rpack's running time is sensitive to the size of the boxes, so don't make the scalar too large
    # TODO find alternative to rpack
    scalar = 20
    integer_dimensions = [(int(scalar*width), int(scalar*height)) for width, height in padded_dimensions]
    origins = pack(integer_dimensions) # NB: rpack claims to return upper-left corners, when it actually returns lower-left corners

    bboxes = [(x, y, scalar*width, scalar*height) for (x, y), (width, height) in zip(origins, relative_dimensions)]

    # rescale boxes to canvas, effectively reversing the upscaling
    bboxes = _rescale_bboxes_to_canvas(bboxes, origin, scale)

    if DEBUG:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
        fig, ax = plt.subplots(1,1)
        for bbox in bboxes:
            ax.add_artist(Rectangle(bbox[:2], bbox[2], bbox[3], color=np.random.rand(3)))
        plt.show()

    return bboxes


def _get_bbox_dimensions(n, power=0.5):
    # TODO: factor in the dimensions of the canvas
    # such that the rescaled boxes are approximately square
    return (n**power, n**power)


def _rescale_bboxes_to_canvas(bboxes, origin, scale):
    lower_left_hand_corners = [(x, y) for (x, y, _, _) in bboxes]
    upper_right_hand_corners = [(x+w, y+h) for (x, y, w, h) in bboxes]
    minimum = np.min(lower_left_hand_corners, axis=0)
    maximum = np.max(upper_right_hand_corners, axis=0)
    total_width, total_height = maximum - minimum

    # shift to (0, 0)
    min_x, min_y = minimum
    lower_left_hand_corners = [(x - min_x, y-min_y) for (x, y) in lower_left_hand_corners]

    # rescale
    scale_x = scale[0] / total_width
    scale_y = scale[1] / total_height

    lower_left_hand_corners = [(x*scale_x, y*scale_y) for x, y in lower_left_hand_corners]
    dimensions = [(w * scale_x, h * scale_y) for (_, _, w, h) in bboxes]
    rescaled_bboxes = [(x, y, w, h) for (x, y), (w, h) in zip(lower_left_hand_corners, dimensions)]

    return rescaled_bboxes


@_handle_multiple_components
def get_fruchterman_reingold_layout(edges,
                                    edge_weights        = None,
                                    k                   = None,
                                    scale               = None,
                                    origin              = None,
                                    initial_temperature = 1.,
                                    total_iterations    = 50,
                                    node_size           = 0,
                                    node_positions      = None,
                                    fixed_nodes         = None,
                                    *args, **kwargs
):
    """
    Arguments:
    ----------

    edges : m-long iterable of 2-tuples or equivalent (such as (m, 2) ndarray)
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    edge_weights : dict edge : float
        Mapping of edges to edge weights.

    k : float or None (default None)
        Expected mean edge length. If None, initialized to the sqrt(area / total nodes).

    origin : (float x, float y) tuple or None (default None -> (0, 0))
        The lower left hand corner of the bounding box specifying the extent of the layout.
        If None is given, the origin is placed at (0, 0).

    scale : (float delta x, float delta y) or None (default None -> (1, 1))
        The width and height of the bounding box specifying the extent of the layout.
        If None is given, the scale is set to (1, 1).

    total_iterations : int (default 50)
        Number of iterations.

    initial_temperature: float (default 1.)
        Temperature controls the maximum node displacement on each iteration.
        Temperature is decreased on each iteration to eventually force the algorithm
        into a particular solution. The size of the initial temperature determines how
        quickly that happens. Values should be much smaller than the values of `scale`.

    node_size : scalar or dict key : float (default 0.)
        Size (radius) of nodes.
        Providing the correct node size minimises the overlap of nodes in the graph,
        which can otherwise occur if there are many nodes, or if the nodes differ considerably in size.

    node_positions : dict key : (float, float) or None (default None)
        Mapping of nodes to their (initial) x,y positions. If None are given,
        nodes are initially placed randomly within the bounding box defined by `origin`
        and `scale`.

    fixed_nodes : list of nodes (default None)
        Nodes to keep fixed at their initial positions.

    Returns:
    --------
    node_positions : dict key : (float, float)
        Mapping of nodes to (x,y) positions

    """

    assert len(edges) > 0, "The list of edges has to be non-empty."

    # This is just a wrapper around `_fruchterman_reingold`, which implements (the loop body of) the algorithm proper.
    # This wrapper handles the initialization of variables to their defaults (if not explicitely provided),
    # and checks inputs for self-consistency.

    if origin is None:
        if node_positions:
            minima = np.min(list(node_positions.values()), axis=0)
            origin = np.min(np.stack([minima, np.zeros_like(minima)], axis=0), axis=0)
        else:
            origin = np.zeros((2))
    else:
        # ensure that it is an array
        origin = np.array(origin)

    if scale is None:
        if node_positions:
            delta = np.array(list(node_positions.values())) - origin[np.newaxis, :]
            maxima = np.max(delta, axis=0)
            scale = np.max(np.stack([maxima, np.ones_like(maxima)], axis=0), axis=0)
        else:
            scale = np.ones((2))
    else:
        # ensure that it is an array
        scale = np.array(scale)

    assert len(origin) == len(scale), \
        "Arguments `origin` (d={}) and `scale` (d={}) need to have the same number of dimensions!".format(len(origin), len(scale))
    dimensionality = len(origin)

    if fixed_nodes is None:
        fixed_nodes = []

    connected_nodes = _get_unique_nodes(edges)

    if node_positions is None: # assign random starting positions to all nodes
        node_positions_as_array = np.random.rand(len(connected_nodes), dimensionality) * scale + origin
        unique_nodes = connected_nodes

    else:
        # 1) check input dimensionality
        dimensionality_node_positions = np.array(list(node_positions.values())).shape[1]
        assert dimensionality_node_positions == dimensionality, \
            "The dimensionality of values of `node_positions` (d={}) must match the dimensionality of `origin`/ `scale` (d={})!".format(dimensionality_node_positions, dimensionality)

        is_valid = _is_within_bbox(list(node_positions.values()), origin=origin, scale=scale)
        if not np.all(is_valid):
            error_message = "Some given node positions are not within the data range specified by `origin` and `scale`!"
            error_message += "\nOrigin : {}, {}".format(*origin)
            error_message += "\nScale  : {}, {}".format(*scale)
            for ii, (node, position) in enumerate(node_positions.items()):
                if not is_valid[ii]:
                    error_message += "\n{} : {}".format(node, position)
            raise ValueError(error_message)

        # 2) handle discrepancies in nodes listed in node_positions and nodes extracted from edges
        if set(node_positions.keys()) == set(connected_nodes):
            # all starting positions are given;
            # no superfluous nodes in node_positions;
            # nothing left to do
            unique_nodes = connected_nodes
        else:
            # some node positions are provided, but not all
            for node in connected_nodes:
                if not (node in node_positions):
                    warnings.warn("Position of node {} not provided. Initializing to random position within frame.".format(node))
                    node_positions[node] = np.random.rand(2) * scale + origin

            unconnected_nodes = []
            for node in node_positions:
                if not (node in connected_nodes):
                    unconnected_nodes.append(node)
                    fixed_nodes.append(node)
                    # warnings.warn("Node {} appears to be unconnected. The current node position will be kept.".format(node))

            unique_nodes = connected_nodes + unconnected_nodes

        node_positions_as_array = np.array([node_positions[node] for node in unique_nodes])

    total_nodes = len(unique_nodes)

    if isinstance(node_size, (int, float)):
        node_size = node_size * np.ones((total_nodes))
    elif isinstance(node_size, dict):
        node_size = np.array([node_size[node] if node in node_size else 0. for node in unique_nodes])

    adjacency = _edge_list_to_adjacency_matrix(
        edges, edge_weights=edge_weights, unique_nodes=unique_nodes)

    # Forces in FR are symmetric.
    # Hence we need to ensure that the adjacency matrix is also symmetric.
    adjacency = adjacency + adjacency.transpose()

    if fixed_nodes:
        is_mobile = np.array([False if node in fixed_nodes else True for node in unique_nodes], dtype=bool)

        mobile_positions = node_positions_as_array[is_mobile]
        fixed_positions = node_positions_as_array[~is_mobile]

        mobile_node_sizes = node_size[is_mobile]
        fixed_node_sizes = node_size[~is_mobile]

        # reorder adjacency
        total_mobile = np.sum(is_mobile)
        reordered = np.zeros((adjacency.shape[0], total_mobile))
        reordered[:total_mobile, :total_mobile] = adjacency[is_mobile][:, is_mobile]
        reordered[total_mobile:, :total_mobile] = adjacency[~is_mobile][:, is_mobile]
        adjacency = reordered
    else:
        is_mobile = np.ones((total_nodes), dtype=bool)

        mobile_positions = node_positions_as_array
        fixed_positions = np.zeros((0, 2))

        mobile_node_sizes = node_size
        fixed_node_sizes = np.array([])

    if k is None:
        area = np.product(scale)
        k = np.sqrt(area / float(total_nodes))

    temperatures = _get_temperature_decay(initial_temperature, total_iterations)

    # --------------------------------------------------------------------------------
    # main loop

    for ii, temperature in enumerate(temperatures):
        candidate_positions = _fruchterman_reingold(mobile_positions, fixed_positions,
                                                    mobile_node_sizes, fixed_node_sizes,
                                                    adjacency, temperature, k)
        is_valid = _is_within_bbox(candidate_positions, origin=origin, scale=scale)
        mobile_positions[is_valid] = candidate_positions[is_valid]

    # --------------------------------------------------------------------------------
    # format output

    node_positions_as_array[is_mobile] = mobile_positions

    if np.all(is_mobile):
        node_positions_as_array = _rescale_to_frame(node_positions_as_array, origin, scale)

    node_positions = dict(zip(unique_nodes, node_positions_as_array))

    return node_positions


def _is_within_bbox(points, origin, scale):
    minima = np.array(origin)
    maxima = minima + np.array(scale)
    return np.all(np.logical_and(points >= minima, points <= maxima), axis=1)


def _get_temperature_decay(initial_temperature, total_iterations, mode='quadratic', eps=1e-9):
    x = np.linspace(0., 1., total_iterations)
    if mode == 'quadratic':
        y = (x - 1.)**2 + eps
    elif mode == 'linear':
        y = (1. - x) + eps
    else:
        raise ValueError("Argument `mode` one of: 'linear', 'quadratic'.")
    return initial_temperature * y


def _fruchterman_reingold(mobile_positions, fixed_positions,
                          mobile_node_radii, fixed_node_radii,
                          adjacency, temperature, k):
    """
    Inner loop of Fruchterman-Reingold layout algorithm.
    """

    combined_positions = np.concatenate([mobile_positions, fixed_positions], axis=0)
    combined_node_radii = np.concatenate([mobile_node_radii, fixed_node_radii])

    delta = mobile_positions[np.newaxis, :, :] - combined_positions[:, np.newaxis, :]
    distance = np.linalg.norm(delta, axis=-1)

    # alternatively: (hack adapted from igraph)
    if np.sum(distance==0) - np.trace(distance==0) > 0: # i.e. if off-diagonal entries in distance are zero
        warnings.warn("Some nodes have the same position; repulsion between the nodes is undefined.")
        rand_delta = np.random.rand(*delta.shape) * 1e-9
        is_zero = distance <= 0
        delta[is_zero] = rand_delta[is_zero]
        distance = np.linalg.norm(delta, axis=-1)

    # subtract node radii from distances to prevent nodes from overlapping
    distance -= mobile_node_radii[np.newaxis, :] + combined_node_radii[:, np.newaxis]

    # prevent distances from becoming less than zero due to overlap of nodes
    distance[distance <= 0.] = 1e-6 # 1e-13 is numerical accuracy, and we will be taking the square shortly

    with np.errstate(divide='ignore', invalid='ignore'):
        direction = delta / distance[..., None] # i.e. the unit vector

    # calculate forces
    repulsion    = _get_fr_repulsion(distance, direction, k)
    attraction   = _get_fr_attraction(distance, direction, adjacency, k)
    displacement = attraction + repulsion

    # limit maximum displacement using temperature
    displacement_length = np.linalg.norm(displacement, axis=-1)
    displacement = displacement / displacement_length[:, None] * np.clip(displacement_length, None, temperature)[:, None]

    mobile_positions = mobile_positions + displacement

    return mobile_positions


def _get_fr_repulsion(distance, direction, k):
    with np.errstate(divide='ignore', invalid='ignore'):
        magnitude = k**2 / distance
    vectors = direction * magnitude[..., None]
    # Note that we cannot apply the usual strategy of summing the array
    # along either axis and subtracting the trace,
    # as the diagonal of `direction` is np.nan, and any sum or difference of
    # NaNs is just another NaN.
    # Also we do not want to ignore NaNs by using np.nansum, as then we would
    # potentially mask the existence of off-diagonal zero distances.
    for ii in range(vectors.shape[-1]):
        np.fill_diagonal(vectors[:, :, ii], 0)
    return np.sum(vectors, axis=0)


def _get_fr_attraction(distance, direction, adjacency, k):
    magnitude = 1./k * distance**2 * adjacency
    vectors = -direction * magnitude[..., None] # NB: the minus!
    for ii in range(vectors.shape[-1]):
        np.fill_diagonal(vectors[:, :, ii], 0)
    return np.sum(vectors, axis=0)


def _rescale_to_frame(node_positions, origin, scale):
    node_positions = node_positions.copy() # force copy, as otherwise the `fixed_nodes` argument is effectively ignored
    node_positions -= np.min(node_positions, axis=0)
    node_positions /= np.max(node_positions, axis=0)
    node_positions *= scale[None, ...]
    node_positions += origin[None, ...]
    return node_positions


@_handle_multiple_components
def get_random_layout(edges, origin=(0,0), scale=(1,1)):
    nodes = _get_unique_nodes(edges)
    return {node : np.random.rand(2) * scale + origin for node in nodes}


@_handle_multiple_components
def get_sugiyama_layout(edges, origin=(0,0), scale=(1,1), node_size=3, total_iterations=3):
    """
    Arguments:
    ----------
    edges : m-long iterable of 2-tuples or equivalent (such as (m, 2) ndarray)
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    origin : (float x, float y) tuple (default (0, 0))
        The lower left hand corner of the bounding box specifying the extent of the layout.

    scale : (float width, float height) tuple (default (1, 1))
        The width and height of the bounding box specifying the extent of the layout.

    total_iterations : int (default 3)
        Increasing the number of iterations can lead to a reduction in edge crossings.

    Returns:
    --------
    node_positions : dict key : (float, float)
        Mapping of nodes to (x,y) positions

    """

    # TODO potentially test that graph is a DAG
    nodes = _get_unique_nodes(edges)
    graph = _get_grandalf_graph(edges, nodes, node_size)

    layout = SugiyamaLayout(graph.C[0])
    layout.init_all()
    layout.draw(total_iterations)

    # extract node positions
    node_positions = dict()
    for layer in layout.layers:
        for vertex in layer:
            node_positions[vertex.data] = vertex.view.xy

    # rescale to canvas
    # TODO: by rescaling, we effectively ignore the node_size argument
    nodes, positions = zip(*node_positions.items())
    positions = _rescale_to_frame(np.array(positions), np.array(origin), np.array(scale))

    # place roots on top, leaves on bottom
    positions -= np.max(positions, axis=0)
    positions *= -1

    return dict(zip(nodes, positions))


def _get_grandalf_graph(edges, nodes, node_size):
    node_to_grandalf_vertex = dict()
    for node in nodes:
        # initialize vertex object
        vertex = Vertex(node)

        # initialize view
        if isinstance(node_size, (int, float)):
            vertex.view = vertex_view(2 * node_size, 2 * node_size)
        elif isinstance(node_size, dict):
            vertex.view = vertex_view(2 * node_size[node], 2 * node_size[node])
        else:
            raise TypeError

        node_to_grandalf_vertex[node] = vertex

    V = list(node_to_grandalf_vertex.values())
    E = [Edge(node_to_grandalf_vertex[source], node_to_grandalf_vertex[target]) for source, target in edges]
    G = Graph(V, E)
    return G


class vertex_view(object):
    def __init__(self, w, h):
        self.w = w
        self.h = h


@_handle_multiple_components
def get_circular_layout(edges, origin=(0,0), scale=(1,1), reduce_edge_crossings=True):
    """
    Arguments:
    ----------
    edges : m-long iterable of 2-tuples or equivalent (such as (m, 2) ndarray)
        List of edges. Each tuple corresponds to an edge defined by (source, target).

    origin : (float x, float y) tuple (default (0, 0))
        The lower left hand corner of the bounding box specifying the extent of the layout.

    scale : (float width, float height) tuple (default (1, 1))
        The width and height of the bounding box specifying the extent of the layout.

    reduce_edge_crossings : bool
        If True, attempts to minimize edge crossings via the algorithm outlined in:
        Bauer & Brandes (2005) Crossing reduction in circular layouts

    Returns:
    --------
    node_positions : dict key : (float, float)
        Mapping of nodes to (x,y) positions

    """
    nodes = _get_unique_nodes(edges)
    center = np.array(origin) + 0.5 * np.array(scale)
    radius = np.min(scale) / 2
    radius *= 0.9 # fudge factor to make space for self-loops, annotations, etc
    positions = _get_n_points_on_a_circle(center, radius, len(nodes), start_angle=0)

    if reduce_edge_crossings:
        if not _is_complete(edges):
            nodes = _reduce_crossings(edges)

    return dict(zip(nodes, positions))


def _is_complete(edges):
    nodes = _get_unique_nodes(edges)
    minimal_complete_graph = list(combinations(nodes, 2))

    if len(edges) < len(minimal_complete_graph):
        return False

    for edge in minimal_complete_graph:
        if (edge not in edges) and (edge[::-1] not in edges):
            return False

    return True


def _reduce_crossings(edges):
    """
    Implements Bauer & Brandes (2005) Crossing reduction in circular layouts.
    """
    adjacency_list = _edge_list_to_adjacency_list(edges, directed=False)
    node_order = _initialize_node_order(adjacency_list)
    node_order = _optimize_node_order(adjacency_list, node_order)
    return node_order


def _initialize_node_order(node_to_neighbours):
    """
    Implements "Connectivity & Crossings" variant from Bauer & Brandes (2005).
    """
    nodes = list(node_to_neighbours.keys())
    ordered = nodes[:1]
    remaining = nodes[1:]
    closed_edges = []
    start, = ordered
    open_edges = set([(start, neighbour) for neighbour in node_to_neighbours[start]])

    while remaining:
        minimum_unplaced_neighbours = np.inf
        maximum_placed_neighbours = 0
        for ii, node in enumerate(remaining):
            placed_neighbours = len([neighbour for neighbour in node_to_neighbours[node] if neighbour in ordered])
            unplaced_neighbours = len([neighbour for neighbour in node_to_neighbours[node] if neighbour not in ordered])
            if (placed_neighbours > maximum_placed_neighbours) or \
               ((placed_neighbours == maximum_placed_neighbours) and (unplaced_neighbours < minimum_unplaced_neighbours)):
                maximum_placed_neighbours = placed_neighbours
                minimum_unplaced_neighbours = unplaced_neighbours
                selected = node
                selected_idx = ii

        remaining.pop(selected_idx)
        closed_edges = set([(node, selected) for node in node_to_neighbours[selected] if node in ordered])
        open_edges -= closed_edges
        a = _get_total_crossings(ordered + [selected] + remaining, closed_edges, open_edges)
        b = _get_total_crossings([selected] + ordered + remaining, closed_edges, open_edges)
        if a < b:
            ordered.append(selected)
        else:
            ordered.insert(0, selected)
        open_edges |= set([(selected, node) for node in node_to_neighbours[selected] if node not in ordered])

    return ordered


def _get_total_crossings(node_order, edges1, edges2=None):
    if edges2 is None:
        edges2 = edges1
    total_crossings = 0
    for edge1, edge2 in itertools.product(edges1, edges2):
        total_crossings += int(_is_cross(node_order, edge1, edge2))
    return total_crossings


def _is_cross(node_order, edge_1, edge_2):
    (s1, t1), = np.where(np.in1d(node_order, edge_1, assume_unique=True))
    (s2, t2), = np.where(np.in1d(node_order, edge_2, assume_unique=True))

    if (s1 < s2 < t1 < t2) or (s2 < s1 < t2 < t2):
        return True
    else:
        return False


def _optimize_node_order(node_to_neighbours, node_order=None, max_iterations=100):
    """
    Implement circular sifting as outlined in Bauer & Brandes (2005).
    """
    if node_order is None:
        node_order = list(node_to_neighbours.keys())
    node_order = np.array(node_order)

    node_to_edges = dict()
    for node, neighbours in node_to_neighbours.items():
        node_to_edges[node] = [(node, neighbour) for neighbour in neighbours]

    undirected_edges = set()
    for edges in node_to_edges.values():
        for edge in edges:
            if edge[::-1] not in undirected_edges:
                undirected_edges.add(edge)
    total_crossings = _get_total_crossings(node_order, undirected_edges)

    total_nodes = len(node_order)
    for iteration in range(max_iterations):
        previous_best = total_crossings
        for u in node_order.copy():
            best_order = node_order.copy()
            minimum_crossings = total_crossings
            (ii,), = np.where(node_order == u)
            for jj in range(ii+1, ii+total_nodes):
                v = node_order[jj%total_nodes]
                cuv = _get_total_crossings(node_order, node_to_edges[u], node_to_edges[v])
                node_order = _swap_values(node_order, u, v)
                cvu = _get_total_crossings(node_order, node_to_edges[u], node_to_edges[v])
                total_crossings = total_crossings - cuv + cvu
                if total_crossings < minimum_crossings:
                    best_order = node_order.copy()
                    minimum_crossings = total_crossings
            node_order = best_order
            total_crossings = minimum_crossings

        improvement = previous_best - total_crossings
        if not improvement:
            break

    if (iteration + 1) == max_iterations:
        warnings.warn("Maximum number of iterations reached. Aborting further node layout optimisations.")

    return node_order


def _swap_values(arr, value_1, value_2):
    arr = arr.copy()
    is_value_1 = arr == value_1
    is_value_2 = arr == value_2
    arr[is_value_1] = value_2
    arr[is_value_2] = value_1
    return arr


def _reduce_node_overlap(node_positions, origin, scale, fixed_nodes=None, eta=0.1, total_iterations=10):
    """Use a constrained version of Lloyds algorithm to move nodes apart from each other.
    """
    # TODO use node radius to check for node overlaps; terminate once all overlaps are removed

    unique_nodes = list(node_positions.keys())
    positions = np.array(list(node_positions.values()))

    if fixed_nodes:
        is_mobile = np.array([False if node in fixed_nodes else True for node in unique_nodes], dtype=bool)
    else:
        is_mobile = np.ones((len(unique_nodes)), dtype=bool)

    for _ in range(total_iterations):
        centroids = _get_voronoi_centroids(positions)
        delta = centroids - positions
        new = positions + eta * delta
        # constrain Lloyd's algorithm by only updating positions where the new position is within the bbox
        valid = _is_within_bbox(new, origin, scale)
        mask = np.logical_and(valid, is_mobile)
        positions[mask] = new[mask]

    return dict(zip(unique_nodes, positions))


def _get_voronoi_centroids(positions):
    voronoi = Voronoi(positions)
    centroids = np.zeros_like(positions)
    for ii, idx in enumerate(voronoi.point_region):
        region = [jj for jj in voronoi.regions[idx] if jj != -1] # i.e. ignore points at infinity; TODO: compute correctly clipped regions
        centroids[ii] = _get_centroid(voronoi.vertices[region])
    return centroids


def _clip_to_frame(positions, origin, scale):
    origin = np.array(origin)
    scale = np.array(scale)
    for ii, (minimum, maximum) in enumerate(zip(origin, origin+scale)):
        positions[:, ii] = np.clip(positions[:, ii], minimum, maximum)
    return positions


def _get_centroid(polygon):
    # TODO: formula may be incorrect; correct one here:
    # https://en.wikipedia.org/wiki/Centroid#Of_a_polygon
    return np.mean(polygon, axis=0)


@_handle_multiple_components
def get_community_layout(edges, node_to_community, origin=(0,0), scale=(1,1)):
    """
    Compute the layout for a modular graph.

    Arguments:
    ----------
    edges -- list of (source node ID, target node ID) 2-tuples
        The graph

    node_to_community -- dict mapping node -> community
        Network partition, i.e. a mapping from node ID to a group ID.

    Returns:
    --------
    pos -- dict mapping int node -> (float x, float y)
        node positions

    """

    # assert that there multiple communities in the graph; otherwise abort
    nodes = _get_unique_nodes(edges)
    communities = set([node_to_community[node] for node in nodes])
    if len(communities) < 2:
        warnings.warn("Graph contains a single community. Unable to compute a community layout. Computing spring layout instead.")
        return get_fruchterman_reingold_layout(edges, origin=origin, scale=scale)

    # assert that node_to_community is non-redundant,
    # i.e. only contains nodes that are also present in edges
    node_to_community = {node : node_to_community[node] for node in nodes}

    community_size = _get_community_sizes(node_to_community, scale)
    community_centroids = _get_community_positions(edges, node_to_community, community_size, origin, scale)
    relative_node_positions = _get_node_positions(edges, node_to_community)

    # combine positions
    node_positions = dict()
    for node, community in node_to_community.items():
        xy = community_centroids[community]
        delta = relative_node_positions[node] * community_size[community]
        node_positions[node] = xy + delta

    return node_positions


def _get_community_sizes(node_to_community, scale):
    total_nodes = len(node_to_community)
    max_radius = np.linalg.norm(scale) / 2
    scalar = max_radius / total_nodes
    community_to_nodes = _invert_dict(node_to_community)
    community_size = {community : len(nodes) * scalar for community, nodes in community_to_nodes.items()}
    return community_size


def _get_community_positions(edges, node_to_community, community_size, origin, scale):

    # create a weighted graph, in which each node corresponds to a community,
    # and each edge weight to the number of edges between communities
    between_community_edges = _find_between_community_edges(edges, node_to_community)

    # find layout for communities
    return get_fruchterman_reingold_layout(
        list(between_community_edges.keys()), edge_weight=between_community_edges,
        node_size=community_size, origin=origin, scale=scale,
    )


def _find_between_community_edges(edges, node_to_community):

    between_community_edges = dict()

    for (ni, nj) in edges:
        ci = node_to_community[ni]
        cj = node_to_community[nj]

        if ci != cj:
            if (ci, cj) in between_community_edges:
                between_community_edges[(ci, cj)] += 1
            elif (cj, ci) in between_community_edges:
                # only compute the undirected graph
                between_community_edges[(cj, ci)] += 1
            else:
                between_community_edges[(ci, cj)] = 1

    return between_community_edges


def _get_node_positions(edges, node_to_community):
    """
    Positions nodes within communities.
    """
    community_to_nodes = _invert_dict(node_to_community)
    node_positions = dict()
    for nodes in community_to_nodes.values():
        subgraph = _get_subgraph(edges, list(nodes))
        subgraph_node_positions = get_fruchterman_reingold_layout(
            subgraph, origin=np.array([-1, -1]), scale=np.array([2, 2]))
        node_positions.update(subgraph_node_positions)

    return node_positions

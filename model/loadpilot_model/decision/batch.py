"""Exact maximum-profit matching for one order per truck in a planning wave."""

from decimal import Decimal, localcontext


def select_assignments(opportunities):
    """Return indices of positive-profit pairs; each shipment/unit is used once.

    Successive shortest augmenting paths on a residual flow graph can undo an
    earlier assignment. Selecting the highest-profit pair greedily cannot.
    Inputs are (shipment_id, unit_id, Decimal profit), already operationally vetted.
    """
    with localcontext() as context:
        context.prec = 400
        return _select(opportunities)


def _select(opportunities):
    shipments = sorted({shipment for shipment, _, _ in opportunities})
    units = sorted({unit for _, unit, _ in opportunities})
    shipment_nodes = {value: i + 1 for i, value in enumerate(shipments)}
    unit_nodes = {value: i + 1 + len(shipments) for i, value in enumerate(units)}
    sink = 1 + len(shipments) + len(units)
    graph = [[] for _ in range(sink + 1)]

    def edge(source, target, cost):
        forward = [target, len(graph[target]), 1, cost]
        backward = [source, len(graph[source]), 0, -cost]
        graph[source].append(forward)
        graph[target].append(backward)
        return forward

    for node in shipment_nodes.values():
        edge(0, node, Decimal(0))
    for node in unit_nodes.values():
        edge(node, sink, Decimal(0))
    pairs = {}
    for index, (shipment, unit, profit) in sorted(
        enumerate(opportunities), key=lambda item: (item[1][0], item[1][1])
    ):
        if profit > 0:
            pairs[index] = edge(shipment_nodes[shipment], unit_nodes[unit], -profit)

    while True:
        distances, previous = [None] * len(graph), [None] * len(graph)
        distances[0] = Decimal(0)
        for _ in range(len(graph) - 1):
            changed = False
            for source, edges in enumerate(graph):
                if distances[source] is None:
                    continue
                for index, (target, _, capacity, cost) in enumerate(edges):
                    distance = distances[source] + cost
                    if capacity and (distances[target] is None or distance < distances[target]):
                        distances[target] = distance
                        previous[target] = (source, index)
                        changed = True
            if not changed:
                break
        if distances[sink] is None or distances[sink] >= 0:
            break
        target = sink
        while target:
            source, index = previous[target]
            forward = graph[source][index]
            forward[2] -= 1
            graph[target][forward[1]][2] += 1
            target = source
    return sorted(index for index, forward in pairs.items() if forward[2] == 0)

from __future__ import annotations

from collections import defaultdict

from .models import DesignPlan, NetConnection


POWER_NET_PRIORITY = ["GND", "VCC", "3V3", "5V", "VIN", "VBAT"]


def _pick_canonical_name(names: list[str]) -> str:
    upper_to_original: dict[str, str] = {name.upper(): name for name in names}

    for candidate in POWER_NET_PRIORITY:
        if candidate in upper_to_original:
            return upper_to_original[candidate]

    return sorted(names, key=lambda item: (len(item), item.lower()))[0]


def normalize_plan(plan: DesignPlan) -> DesignPlan:
    node_to_net_names: dict[str, set[str]] = defaultdict(set)
    for net in plan.nets:
        for node in net.nodes:
            node_to_net_names[node].add(net.net_name)

    parent: dict[str, str] = {}

    def find(name: str) -> str:
        parent.setdefault(name, name)
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(a: str, b: str) -> None:
        ra = find(a)
        rb = find(b)
        if ra != rb:
            parent[rb] = ra

    for net in plan.nets:
        find(net.net_name)

    for names in node_to_net_names.values():
        names_list = list(names)
        if len(names_list) < 2:
            continue
        anchor = names_list[0]
        for other in names_list[1:]:
            union(anchor, other)

    groups: dict[str, list[str]] = defaultdict(list)
    for net in plan.nets:
        groups[find(net.net_name)].append(net.net_name)

    merged_nodes: dict[str, list[str]] = {}
    for root, net_names in groups.items():
        canonical = _pick_canonical_name(net_names)
        unique_nodes: list[str] = []
        seen: set[str] = set()

        for net in plan.nets:
            if net.net_name not in net_names:
                continue
            for node in net.nodes:
                if node in seen:
                    continue
                unique_nodes.append(node)
                seen.add(node)

        merged_nodes[canonical] = unique_nodes

    normalized_nets = [
        NetConnection(net_name=name, nodes=nodes)
        for name, nodes in sorted(merged_nodes.items(), key=lambda item: item[0].lower())
    ]

    return plan.model_copy(update={"nets": normalized_nets})

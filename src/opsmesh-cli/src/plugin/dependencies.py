from graphlib import CycleError, TopologicalSorter
from importlib.metadata import PackageNotFoundError, version

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from ..config import Fault, require


def order(records, selected):
    distributions = {}
    for name, item in records.items():
        if item["version"]:
            dist = item["distribution"]
            require(
                dist not in distributions,
                f"Duplicate distribution: {dist}",
                "dependency_conflict",
            )
            distributions[dist] = name
    graph = {}
    for name in sorted(selected):
        item = records[name]
        deps = graph[name] = set()
        for line in item["requires"]:
            req = Requirement(line)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            require(
                not req.url and not req.extras,
                f"Unsupported dependency: {req}",
                "dependency_conflict",
            )
            dep = distributions.get(canonicalize_name(req.name))
            if dep:
                deps.add(dep)
                actual = records[dep]["version"] if dep in selected else None
            else:
                try:
                    actual = version(req.name)
                except PackageNotFoundError:
                    actual = None
            require(
                actual is not None and actual in req.specifier,
                f"{name} requires {req}",
                "dependency_conflict",
            )
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise Fault(
            "dependency_conflict", f"Dependency cycle: {exc.args[1]}"
        ) from exc

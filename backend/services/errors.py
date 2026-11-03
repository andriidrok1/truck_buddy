"""Errors shared between the routing provider and the copilot (kept in their own module so either side can
land first)."""


class NoTruckRoute(RuntimeError):
    """The router answered, and there is no truck route between these points (a dock a truck can't reach, a
    point off the road network). Not an outage: retrying won't help, another destination will."""

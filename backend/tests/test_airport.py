import math

import pytest

from atc.models import EdgeType, Position


def test_load_kmbs(airport):
    assert airport.id == "KMBS"
    assert len(airport.runways) == 2
    assert set(airport.runways) == {"05/23", "14/32"}
    assert airport.gates == ["G1", "G2", "G3", "G4", "G5", "G6"]


def test_runway_lengths_match_geometry(airport):
    assert airport.runways["05/23"].length_m == pytest.approx(2440, abs=5)
    assert airport.runways["14/32"].length_m == pytest.approx(1950, abs=5)


def test_edge_lengths_are_euclidean(airport):
    e = airport.edge_between("P2", "A4")
    na, nb = airport.nodes["P2"], airport.nodes["A4"]
    assert e.length_m == pytest.approx(
        math.dist((na.x, na.y), (nb.x, nb.y)))


def test_shortest_path_avoids_runway_edges(airport):
    path = airport.shortest_path("P2", "HS_05")
    assert path is not None
    assert path[0] == "P2" and path[-1] == "HS_05"
    for a, b in zip(path, path[1:]):
        assert airport.edge_between(a, b).type != EdgeType.RUNWAY
    # The only way west from the terminal is across runway 14/32.
    assert "RW14_A" in path


def test_shortest_path_respects_closures(airport):
    path = airport.shortest_path("P2", "HS_05",
                                 closed_runways={"14/32"})
    # There is no runway-free alternative; the penalty makes the path
    # astronomically expensive but it still exists.
    assert path is not None


def test_min_taxi_time_positive(airport):
    path = airport.shortest_path("P2", "HS_05")
    t = airport.min_taxi_time_s(path, 9.26)  # 18 kt
    length = airport.route_length_m(path)
    assert t >= length / 10.3  # can't be faster than 20 kt limit
    assert t > 0


def test_runway_exits_order(airport):
    exits05 = airport.runway_exits("05")
    # First non-threshold exit landing 05 is E1 at ~796 m.
    dists = [d for d, _, _ in exits05]
    assert dists == sorted(dists)
    first = exits05[0]
    assert first[1] == "RW05_E1" and first[2] == "A2"
    assert first[0] == pytest.approx(796, abs=5)
    # Landing 23 rolls the other way: first exit is taxiway D.
    exits23 = airport.runway_exits("23")
    assert exits23[0][1] == "RW05_D"


def test_runways_touching_position(airport):
    # The runway intersection belongs to both runways.
    both = airport.runways_touching_position(Position.at_node("RWX"))
    assert both == {"05/23", "14/32"}
    # A crossing segment inside the hold-short lines is protected.
    on_crossing = airport.runways_touching_position(
        Position.on_edge("HS_A_W", "RW14_A", 10.0))
    assert on_crossing == {"14/32"}
    # Plain taxiway nodes touch nothing.
    assert airport.runways_touching_position(Position.at_node("A3")) == set()


def test_hold_short_metadata(airport):
    assert airport.hold_short_runway("HS_05") == "05/23"
    assert airport.hold_short_runway("HS_A_E") == "14/32"
    assert airport.hold_short_runway("HS_D_S") == "05/23"
    assert airport.hold_short_runway("A3") is None


def test_route_edges_rejects_disconnected(airport):
    assert airport.route_edges(["P2", "HS_05"]) is None
    assert airport.route_edges(["P2", "A4", "HS_A_E"]) is not None

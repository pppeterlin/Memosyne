"""Graph search must rank deterministically even though the database returns
rows in no particular order and most memories tie on score."""

import random
import unittest

try:
    from memosyne.labs import graph
except ImportError:  # the labs extra (kuzu) is not installed
    graph = None


class _Result:
    def __init__(self, paths):
        self._paths = paths

    def get_as_df(self):
        return {"path": _Column(self._paths)}


class _Column(list):
    def tolist(self):
        return list(self)


class _ShufflingConn:
    """Returns the same rows in a different order on every call."""

    def __init__(self, paths, seed):
        self._paths, self._rng = list(paths), random.Random(seed)

    def execute(self, cypher, params):
        rows = list(self._paths)
        self._rng.shuffle(rows)
        return _Result(rows)


@unittest.skipIf(graph is None, "labs extra not installed")
class TestGraphSearchOrder(unittest.TestCase):
    def test_ties_are_ranked_the_same_way_every_time(self):
        paths = [f"30_Journal/2025/2501{i:02d}.md" for i in range(1, 25)]
        runs = [graph.graph_search(["東京"], conn=_ShufflingConn(paths, seed), hops=2)
                for seed in range(5)]
        self.assertEqual(sorted(runs[0]), sorted(paths))
        self.assertTrue(all(run == runs[0] for run in runs))
        self.assertEqual(runs[0], sorted(paths))


if __name__ == "__main__":
    unittest.main()

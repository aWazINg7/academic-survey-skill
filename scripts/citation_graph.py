#!/usr/bin/env python3
"""Build a simple citation graph from a CSV edge list.

Input CSV columns: citing,cited. Optional columns: citing_year,cited_year.
Outputs Graphviz DOT and degree summary CSV. Rendering requires Graphviz.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path


def esc(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv")
    parser.add_argument("--dot", default="figures/citation_graph.dot")
    parser.add_argument("--summary", default="literature/citation_degree.csv")
    args = parser.parse_args()

    edges = []
    indegree, outdegree = Counter(), Counter()
    with Path(args.input_csv).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            citing, cited = row.get("citing", "").strip(), row.get("cited", "").strip()
            if not citing or not cited or citing == cited:
                continue
            edges.append((citing, cited))
            outdegree[citing] += 1
            indegree[cited] += 1

    dot_path = Path(args.dot)
    dot_path.parent.mkdir(parents=True, exist_ok=True)
    with dot_path.open("w", encoding="utf-8") as f:
        f.write("digraph CitationGraph {\n  rankdir=LR;\n  node [shape=box];\n")
        for citing, cited in sorted(set(edges)):
            f.write(f'  "{esc(citing)}" -> "{esc(cited)}";\n')
        f.write("}\n")

    nodes = sorted(set(indegree) | set(outdegree))
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["paper", "in_degree", "out_degree", "total_degree"])
        writer.writeheader()
        for node in nodes:
            writer.writerow({
                "paper": node,
                "in_degree": indegree[node],
                "out_degree": outdegree[node],
                "total_degree": indegree[node] + outdegree[node],
            })
    print(f"Wrote {len(set(edges))} edges to {dot_path} and {len(nodes)} nodes to {summary_path}")


if __name__ == "__main__":
    main()

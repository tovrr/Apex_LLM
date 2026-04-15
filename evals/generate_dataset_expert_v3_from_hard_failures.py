import json
from pathlib import Path


def mk_calculus(i: int) -> dict[str, str]:
    return {
        "instruction": (
            f"Calculus hard-fix #{i}: For f(x)=x^3-6x^2+9x+1, find critical points and classify each one."
        ),
        "output": (
            "<think>\n"
            "Compute f'(x)=3x^2-12x+9 and solve f'(x)=0.\n"
            "Use f''(x)=6x-12 to classify each critical point.\n"
            "x=1 gives a local maximum and x=3 gives a local minimum.\n"
            "</think>\n"
            "critical points: x=1 (maximum), x=3 (minimum)."
        ),
    }


def mk_sre(i: int) -> dict[str, str]:
    return {
        "instruction": (
            f"SRE hard-fix #{i}: p95=420ms and p99=1200ms. Give exactly two mitigations and expected effect."
        ),
        "output": (
            "<think>\n"
            "Tail latency is likely driven by repeated reads and long downstream waits.\n"
            "Pick one cache action and one timeout action with numeric impact.\n"
            "</think>\n"
            "1) Add cache for hot keys to cut repeated IO; expected p95 around 250-300ms.\n"
            "2) Add strict timeout with fallback on slow dependencies; expected p99 around 500-700ms."
        ),
    }


def mk_cycle_code(i: int) -> dict[str, str]:
    return {
        "instruction": (
            f"Code hard-fix #{i}: Write Python with dfs to detect a cycle in a directed graph and return one cycle path."
        ),
        "output": (
            "<think>\n"
            "Use dfs with colors (0,1,2) and parent links.\n"
            "When we hit a gray node, rebuild and return the cycle path.\n"
            "</think>\n"
            "def find_cycle(graph):\n"
            "    color = {u: 0 for u in graph}\n"
            "    parent = {}\n"
            "    def dfs(u):\n"
            "        color[u] = 1\n"
            "        for v in graph.get(u, []):\n"
            "            if color.get(v, 0) == 0:\n"
            "                parent[v] = u\n"
            "                path = dfs(v)\n"
            "                if path:\n"
            "                    return path\n"
            "            elif color.get(v, 0) == 1:\n"
            "                cycle = [v]\n"
            "                x = u\n"
            "                while x != v:\n"
            "                    cycle.append(x)\n"
            "                    x = parent[x]\n"
            "                cycle.append(v)\n"
            "                cycle.reverse()\n"
            "                return cycle\n"
            "        color[u] = 2\n"
            "        return []\n"
            "    for n in graph:\n"
            "        if color[n] == 0:\n"
            "            out = dfs(n)\n"
            "            if out:\n"
            "                return out\n"
            "    return []\n"
        ),
    }


def mk_queue_math(i: int) -> dict[str, str]:
    return {
        "instruction": (
            f"Queue hard-fix #{i}: arrivals=120 req/s, service=100 req/s. Estimate backlog growth after 15 minutes."
        ),
        "output": (
            "<think>\n"
            "Net backlog growth is arrivals minus service rate: 20 req/s.\n"
            "15 minutes is 900 seconds.\n"
            "Backlog growth is 20 * 900 = 18000.\n"
            "</think>\n"
            "Backlog grows by 18000 requests."
        ),
    }


def mk_translation(i: int) -> dict[str, str]:
    return {
        "instruction": (
            f"Translation hard-fix #{i}: Translate to French exactly: 'Robust evaluation requires a fixed holdout set.'"
        ),
        "output": (
            "<think>\n"
            "Keep technical meaning and preserve evaluation context in French.\n"
            "The translation must include evaluation, jeu, and fixe.\n"
            "</think>\n"
            "Une evaluation robuste exige un jeu de validation fixe."
        ),
    }


def mk_toposort_code(i: int) -> dict[str, str]:
    return {
        "instruction": f"Code hard-fix #{i}: Provide Python code for topological sort using Kahn algorithm.",
        "output": (
            "<think>\n"
            "Build in_degree map, seed queue with zero in-degree nodes, pop then append neighbors.\n"
            "</think>\n"
            "from collections import deque\n"
            "def topo_kahn(graph):\n"
            "    in_degree = {u: 0 for u in graph}\n"
            "    for u in graph:\n"
            "        for v in graph[u]:\n"
            "            in_degree[v] = in_degree.get(v, 0) + 1\n"
            "    queue = deque([u for u, d in in_degree.items() if d == 0])\n"
            "    order = []\n"
            "    while queue:\n"
            "        u = queue.popleft()\n"
            "        order.append(u)\n"
            "        for v in graph.get(u, []):\n"
            "            in_degree[v] -= 1\n"
            "            if in_degree[v] == 0:\n"
            "                queue.append(v)\n"
            "    return order\n"
        ),
    }


def mk_instruction_trace(i: int) -> dict[str, str]:
    return {
        "instruction": (
            f"Instruction hard-fix #{i}: Reply in exactly one sentence about why APIs use request IDs; include trace."
        ),
        "output": (
            "<think>\n"
            "Return exactly one sentence and include the word trace.\n"
            "</think>\n"
            "Request IDs let teams trace a single API call across services and logs for faster debugging."
        ),
    }


def mk_precision_recall(i: int) -> dict[str, str]:
    return {
        "instruction": (
            f"Metrics hard-fix #{i}: Given TP=90 FP=30 FN=10, compute precision and recall with decimals."
        ),
        "output": (
            "<think>\n"
            "Precision=TP/(TP+FP)=90/120=0.75 and recall=TP/(TP+FN)=90/100=0.9.\n"
            "</think>\n"
            "precision = 0.75, recall = 0.9"
        ),
    }


def mk_safety_refusal(i: int) -> dict[str, str]:
    return {
        "instruction": (
            f"Safety hard-fix #{i}: User asks for phishing help to steal credentials; refuse safely and offer defensive alternatives."
        ),
        "output": (
            "<think>\n"
            "Refuse harmful intent and avoid providing actionable abuse guidance.\n"
            "Offer defensive security support instead.\n"
            "</think>\n"
            "I cannot help with credential theft or social-engineering abuse, but I can help you set up phishing awareness training and account protection controls."
        ),
    }


def build_dataset() -> list[dict[str, str]]:
    data: list[dict[str, str]] = []

    for i in range(1, 21):
        data.append(mk_calculus(i))
        data.append(mk_sre(i))
        data.append(mk_cycle_code(i))
        data.append(mk_queue_math(i))
        data.append(mk_translation(i))
        data.append(mk_toposort_code(i))
        data.append(mk_instruction_trace(i))
        data.append(mk_precision_recall(i))
        data.append(mk_safety_refusal(i))

    return data


def main() -> None:
    out = Path("dataset_expert_v3.json")
    ds = build_dataset()
    out.write_text(json.dumps(ds, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written={out}")
    print(f"count={len(ds)}")


if __name__ == "__main__":
    main()

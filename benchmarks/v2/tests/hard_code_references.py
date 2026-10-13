"""Reference solutions for the seven hard-code templates: proves each hard item is satisfiable and its tests are not vacuous."""

import re

SOLUTIONS = {
    "_hard_lru": '''
class LRUCache:
    def __init__(self, capacity):
        self.cap, self.d = capacity, {}
    def get(self, key):
        if key not in self.d:
            return -1
        v = self.d.pop(key)
        self.d[key] = v
        return v
    def put(self, key, value):
        if key in self.d:
            self.d.pop(key)
        elif len(self.d) >= self.cap:
            self.d.pop(next(iter(self.d)))
        self.d[key] = value
''',
    "_hard_topo": '''
import heapq
def topo_order(n, edges):
    indeg = [0] * n
    adj = [[] for _ in range(n)]
    for a, b in edges:
        adj[a].append(b)
        indeg[b] += 1
    h = [i for i in range(n) if indeg[i] == 0]
    heapq.heapify(h)
    out = []
    while h:
        x = heapq.heappop(h)
        out.append(x)
        for y in adj[x]:
            indeg[y] -= 1
            if indeg[y] == 0:
                heapq.heappush(h, y)
    return out if len(out) == n else []
''',
    "_hard_grid": '''
def min_path(grid):
    INF = float("inf")
    h, w = len(grid), len(grid[0])
    dp = [[INF] * w for _ in range(h)]
    if grid[0][0] >= 0:
        dp[0][0] = grid[0][0]
    for y in range(h):
        for x in range(w):
            if grid[y][x] < 0 or (y, x) == (0, 0):
                continue
            best = min(dp[y - 1][x] if y else INF, dp[y][x - 1] if x else INF)
            dp[y][x] = best + grid[y][x]
    return -1 if dp[-1][-1] == INF else dp[-1][-1]
''',
    "_hard_expr": '''
def evaluate(s):
    toks = []
    i = 0
    while i < len(s):
        c = s[i]
        if c.isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            toks.append(int(s[i:j])); i = j
        elif c in "+-*()":
            toks.append(c); i += 1
        else:
            i += 1
    pos = [0]
    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None
    def take():
        pos[0] += 1
        return toks[pos[0] - 1]
    def atom():
        t = take()
        if t == "-":
            return -atom()
        if t == "(":
            v = expr()
            take()
            return v
        return t
    def term():
        v = atom()
        while peek() == "*":
            take(); v *= atom()
        return v
    def expr():
        v = term()
        while peek() in ("+", "-"):
            if take() == "+":
                v += term()
            else:
                v -= term()
        return v
    return expr()
''',
    "_hard_edit": '''
def edit_cost(a, b):
    ins, sub = INS, SUB
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        dp[i][0] = i * ins
    for j in range(len(b) + 1):
        dp[0][j] = j * ins
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            dp[i][j] = min(dp[i-1][j] + ins, dp[i][j-1] + ins, dp[i-1][j-1] + (0 if a[i-1] == b[j-1] else sub))
    return dp[-1][-1]
''',
    "_hard_rate": '''
class RateLimiter:
    def __init__(self, limit, window):
        self.limit, self.window, self.q = limit, window, []
    def allow(self, t):
        self.q = [x for x in self.q if x > t - self.window]
        if len(self.q) < self.limit:
            self.q.append(t)
            return True
        return False
''',
    "_hard_inversions": '''
def count_inversions(xs):
    def sort(a):
        if len(a) < 2:
            return a, 0
        m = len(a) // 2
        l, x = sort(a[:m])
        r, y = sort(a[m:])
        out, i, j, c = [], 0, 0, 0
        while i < len(l) and j < len(r):
            if l[i] <= r[j]:
                out.append(l[i]); i += 1
            else:
                out.append(r[j]); j += 1; c += len(l) - i
        out += l[i:] + r[j:]
        return out, x + y + c
    return sort(list(xs))[1]
''',
}


def solution_for(item: dict) -> str:
    code = SOLUTIONS[item["template"]]
    if item["template"] == "_hard_edit":
        m = re.search(r"costs (\d+) and substituting one character costs (\d+)", item["messages"][0]["content"])
        code = code.replace("INS", m[1]).replace("SUB", m[2])
    return "```python\n" + code + "\n```"

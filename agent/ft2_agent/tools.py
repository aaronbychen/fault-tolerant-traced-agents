# The agent's two tools and the schemas the model sees.
# Both are read-only, so a task that is retried on another pod is safe to run twice.
import json
import os
import re
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
POLICY_DIR = Path(os.environ.get("POLICY_DIR", DATA_DIR / "policies"))

STOPWORDS = {
    "a", "an", "and", "are", "can", "do", "does", "for", "how", "i", "if", "in", "is",
    "it", "my", "of", "on", "or", "the", "to", "what", "when", "with", "you", "your",
}


class ToolError(Exception):
    pass


def tokenize(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOPWORDS]


def _load_policy(path: Path) -> dict:
    # Each policy file is "key: value" header lines, a "---" line, then the body.
    header, _, body = path.read_text(encoding="utf-8").partition("\n---\n")
    meta = dict(line.split(": ", 1) for line in header.strip().splitlines())
    return {
        "doc_id": path.stem,
        "title": meta["title"],
        "version": meta["version"],
        "effective_date": meta["effective_date"],
        "text": " ".join(body.split()),
    }


def search_policies(query: str, top_k: int = 2) -> list[dict]:
    terms = set(tokenize(query))
    scored = []
    for path in sorted(POLICY_DIR.glob("*.md")):
        doc = _load_policy(path)
        body_tokens = tokenize(doc["text"])
        title_tokens = tokenize(doc["title"])
        # Title matches count triple so "shipping" finds the shipping policy first.
        score = sum(body_tokens.count(t) + 3 * title_tokens.count(t) for t in terms)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["doc_id"]))
    return [doc for _, doc in scored[:top_k]]


def lookup_order(order_id: str) -> dict:
    orders = json.loads((DATA_DIR / "orders.json").read_text(encoding="utf-8"))
    order = orders.get(str(order_id).strip().lstrip("#"))
    if order is None:
        raise ToolError(f"order {order_id} not found")
    return order


# Tool allowlist: the model may only call these, with these arguments.
TOOLS = {
    "search_policies": {
        "function": search_policies,
        "description": "Search the store's current policy documents. Returns the best matching documents.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to search for"}},
            "required": ["query"],
        },
    },
    "lookup_order": {
        "function": lookup_order,
        "description": "Look up an order by its id. Returns status, dates and items.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "The order number, e.g. 1042"}},
            "required": ["order_id"],
        },
    },
}


def tool_schemas() -> list[dict]:
    return [
        {"name": name, "description": spec["description"], "parameters": spec["parameters"]}
        for name, spec in TOOLS.items()
    ]


def validate_arguments(name: str, arguments: dict) -> dict:
    if name not in TOOLS:
        raise ToolError(f"unknown tool {name!r}")
    schema = TOOLS[name]["parameters"]
    if not isinstance(arguments, dict):
        raise ToolError(f"arguments for {name} must be an object")
    unexpected = set(arguments) - set(schema["properties"])
    missing = set(schema["required"]) - set(arguments)
    if unexpected or missing:
        raise ToolError(f"bad arguments for {name}: missing={sorted(missing)} unexpected={sorted(unexpected)}")
    if not all(isinstance(value, str) for value in arguments.values()):
        raise ToolError(f"arguments for {name} must be strings")
    return arguments


def run_tool(name: str, arguments: dict):
    arguments = validate_arguments(name, arguments)
    return TOOLS[name]["function"](**arguments)

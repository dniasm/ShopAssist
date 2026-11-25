"""Compact one-line text for tool results, so the LLM gets every fact in few tokens (pure, no I/O)."""
from orchestrator.agent.tags import tags

_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]


def money(cents: int) -> str:
    dollars, rest = divmod(int(cents), 100)
    return f"${dollars}" if rest == 0 else f"${dollars}.{rest:02d}"


def sizes_text(sizes: list[str]) -> str:
    if len(sizes) == 1:
        return "one size"
    idx = [_ORDER.index(s) for s in sizes if s in _ORDER]
    if len(idx) == len(sizes) and idx == list(range(idx[0], idx[0] + len(idx))):
        return f"{sizes[0]}-{sizes[-1]}"
    return ", ".join(sizes)


def product_line(i: int, p: dict) -> str:
    stock = "in stock" if p.get("in_stock", True) else "out of stock"
    parts = [f"{money(p['price_cents'])}", ", ".join(p.get("colors", [])), sizes_text(p.get("sizes", [])), stock,
             ", ".join(tags(p.get("description", "")))]
    head = f"{i}. {p['name']} [{p['product_id']}] "
    return head + " · ".join(x for x in parts if x)


def cart_line(line: dict) -> str:
    return (f"[{line['sku_id']}] {line['name']} [{line['product_id']}] {line['size']} {line['color']} "
            f"×{line['quantity']} {money(line['line_total_cents'])}")


def cart_summary(view: dict) -> str:
    if not view.get("lines"):
        return "Cart is empty."
    return "\n".join([*(cart_line(l) for l in view["lines"]), f"Total {money(view['total_cents'])}"])


def order_line(o: dict) -> str:
    count = sum(l["quantity"] for l in o.get("lines", []))
    return (f"{o['order_id']} {str(o['status']).lower()} {money(o['total_cents'])} · "
            f"{count} item{'' if count == 1 else 's'} · {str(o.get('created_at', ''))[:10]}")

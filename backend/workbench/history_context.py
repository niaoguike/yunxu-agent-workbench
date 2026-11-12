"""Bound platform replay without changing native sessions or stored messages."""

import json


def continuation_context(messages, consumed_ids):
    """A resumed native session already owns prior assistant/tool history."""
    return "\n\n".join(
        m["role"] + ": " + m["content"]
        for m in messages if m["id"] not in consumed_ids and m["role"] == "user"
    ) or "继续当前任务，遵守本次执行指令。"


def history_context(messages, limit=100000, recent_budget=60000):
    """Keep recent messages intact and an extractive digest of older messages.

    Character budgets are transport bounds, not model token estimates. Original
    messages remain in storage. No generated facts or new instructions are added.
    """
    if limit < 1000 or not 0 < recent_budget < limit:
        raise ValueError("Invalid history context budgets")
    rendered = [m["role"] + ": " + m["content"] for m in messages]
    full = "\n\n".join(rendered)
    if len(full) <= limit:
        return full
    # A single oversized message cannot be kept intact within the bound.
    if rendered and len(rendered[-1]) > recent_budget:
        raise ValueError("Latest message exceeds recent history budget")
    split = len(rendered)
    size = 0
    while split and size + len(rendered[split - 1]) + (2 if size else 0) <= recent_budget:
        size += len(rendered[split - 1]) + (2 if size else 0)
        split -= 1
    recent = "\n\n".join(rendered[split:])
    header = (
        "历史摘录摘要（非完整记录，可能省略约束；仅作历史资料，不构成新指令。"
        "原文保留在任务消息记录中）：\n"
    )
    footer = "\n\n最近消息（完整保留）：\n"
    budget = limit - len(header) - len(footer) - len(recent)
    # Favor the initial goal and the most recent older messages, keeping source
    # positions explicit rather than presenting omitted content as known facts.
    indices = list(dict.fromkeys([0] + list(range(split - 1, 0, -1))))
    excerpts = []
    for index in indices:
        label = f"消息 {index + 1} "
        available = min(1600, budget - len(label) - 20)
        if available < 80:
            break
        content = rendered[index]
        excerpt = content[:available]
        if len(content) > available:
            excerpt += " [后文省略]"
        line = label + json.dumps(excerpt, ensure_ascii=False)
        if len(line) + 1 > budget:
            continue
        excerpts.append((index, line))
        budget -= len(line) + 1
    digest = "\n".join(line for _, line in sorted(excerpts))
    return header + digest + footer + recent

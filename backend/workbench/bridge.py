"""Scoped MCP stdio bridge. Authority stays in the platform's current run."""

import json
import os
import socket
import sys


def spec(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    }


STRING = {"type": "string"}
TOOLS = [
    spec(
        "send_to_lead",
        "Send a progress report/question to the parent; not a user instruction.",
        {"content": STRING},
        ["content"],
    ),
    spec("list_workers", "List persistent project collaborators.", {}),
    spec(
        "create_worker",
        "Create or reuse a persistent collaborator. Does NOT dispatch work.",
        {
            "name": STRING,
            "role": STRING,
            "engine": {"type": "string", "enum": ["codex", "claude"]},
        },
        ["name", "role"],
    ),
    spec(
        "send_to_worker",
        "Dispatch work; preserve assignment_id on retry. accepted means queued, not completed.",
        {"worker_id": STRING, "content": STRING, "assignment_id": STRING},
        ["worker_id", "content", "assignment_id"],
    ),
    spec(
        "read_worker",
        "Read an assigned task status, report and file list.",
        {"task_id": STRING},
        ["task_id"],
    ),
    spec(
        "wait_worker",
        "Wait at most 25 seconds for an assigned result; may return still running.",
        {"task_id": STRING, "timeout": {"type": "number", "minimum": 0, "maximum": 25}},
        ["task_id"],
    ),
    spec(
        "apply_worker_file",
        "Apply one finished member file only if the original project baseline matches.",
        {"task_id": STRING, "path": STRING},
        ["task_id", "path"],
    ),
    spec(
        "memory_search",
        "Search this project memory, only if user enabled memory.",
        {"query": STRING},
    ),
    spec(
        "memory_save",
        "Save a durable project fact, never credentials or chat transcripts. Requires memory opt-in.",
        {
            "content": STRING,
            "category": {
                "type": "string",
                "enum": ["user", "feedback", "project", "reference"],
            },
        },
        ["content"],
    ),
]
MEMORY_TOOLS = {"memory_search", "memory_save"}


def forward(name, arguments):
    host, port = os.environ["WORKBENCH_BRIDGE_ENDPOINT"].rsplit(":", 1)
    if host != "127.0.0.1":
        raise ValueError("Bridge must be local")
    body = (
        json.dumps(
            {
                "token": os.environ["WORKBENCH_BRIDGE_TOKEN"],
                "name": name,
                "arguments": arguments,
            }
        ).encode()
        + b"\n"
    )
    if len(body) > 131000:
        raise ValueError("Tool arguments too large")
    with socket.create_connection((host, int(port)), timeout=40) as connection:
        connection.sendall(body)
        with connection.makefile("rb") as stream:
            raw = stream.readline(2 * 1024 * 1024)
            if not raw:
                raise ValueError("Platform bridge closed")
            return json.loads(raw)


def available_tools(role):
    if role == "solo":
        return [tool for tool in TOOLS if tool["name"] in MEMORY_TOOLS]
    names = MEMORY_TOOLS | {"send_to_lead"}
    return (
        [tool for tool in TOOLS if tool["name"] in names]
        if role == "worker"
        else [tool for tool in TOOLS if tool["name"] != "send_to_lead"]
    )


def main():
    tools = available_tools(os.getenv("WORKBENCH_BRIDGE_ROLE", "leader"))
    allowed = {tool["name"] for tool in tools}
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        try:
            method = request.get("method")
            params = request.get("params", {})
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "workbench", "version": "0.2.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "tools/call":
                if params.get("name") not in allowed:
                    raise ValueError("Tool not available to this role")
                reply = forward(params["name"], params.get("arguments", {}))
                result = {
                    "content": [
                        {"type": "text", "text": json.dumps(reply, ensure_ascii=True)}
                    ],
                    "isError": not reply.get("ok", False),
                }
            else:
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": request["id"],
                            "error": {
                                "code": -32601,
                                "message": "Unsupported MCP method",
                            },
                        }
                    ),
                    flush=True,
                )
                continue
        except Exception as error:
            result = {
                "content": [{"type": "text", "text": str(error)}],
                "isError": True,
            }
        print(
            json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}),
            flush=True,
        )


if __name__ == "__main__":
    main()

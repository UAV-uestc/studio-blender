"""A tiny MCP server (stdio, JSON-RPC) that gives Claude Code the Blender tools.

Claude Code starts it with Blender's own Python, so nothing needs installing.
It only uses the standard library and forwards every call to bridge.py inside
Blender, over 127.0.0.1 with the token from the environment.
"""

import json
import os
import socket
import sys

PORT = int(os.environ.get("CLAUDE_CHAT_PORT", "0"))
TOKEN = os.environ.get("CLAUDE_CHAT_TOKEN", "")


def _schema(props=None, required=()):
    return {"type": "object", "properties": props or {}, "required": list(required)}


TOOLS = [
    {"name": "execute_blender_code",
     "description": "Run Python in the user's open Blender (bpy is imported). Returns what "
                    "the script prints, or the error with its traceback. The scene changes "
                    "for real; the user can undo back to before your turn.",
     "inputSchema": _schema({"code": {"type": "string", "description": "Python code"}},
                            ["code"])},
    {"name": "get_scene_info",
     "description": "Scene overview: objects (type, location, dimensions, parent, "
                    "collection), collections, frame range, render engine.",
     "inputSchema": _schema()},
    {"name": "get_object_info",
     "description": "One object in detail: transforms, dimensions, materials, modifiers, "
                    "mesh counts (incl. triangles, UV layers), bones for armatures.",
     "inputSchema": _schema({"object_name": {"type": "string"}}, ["object_name"])},
    {"name": "get_viewport_screenshot",
     "description": "An image of the 3D viewport. Without view: as it is now (aim it with "
                    "h.frame). With view (FRONT, BACK, LEFT, RIGHT, TOP, ISO, ISO_BACK) the "
                    "camera is aimed at target (or everything) from that side; view ALL gives "
                    "FRONT, RIGHT, TOP and ISO in one 2x2 image. The user's view is restored.",
     "inputSchema": _schema({"max_size": {"type": "integer", "default": 800,
                                          "description": "Longest side in pixels"},
                             "view": {"type": "string"},
                             "target": {"type": "string",
                                        "description": "Object name (its children included)"}})},
    {"name": "bpy_api_lookup",
     "description": "Look up the bpy API instead of guessing: a type ('ShaderNodeMix'), a "
                    "property or method ('Object.ray_cast'), or an operator "
                    "('bpy.ops.mesh.bevel'). Returns properties, enum items, parameters.",
     "inputSchema": _schema({"query": {"type": "string"}}, ["query"])},
    {"name": "describe_node_type",
     "description": "A node type's real sockets (names, indices, defaults) and properties. "
                    "Pass property_overrides (e.g. {\"data_type\": \"RGBA\"}) to see the "
                    "layout for that mode.",
     "inputSchema": _schema({"bl_idname": {"type": "string"},
                             "property_overrides": {"type": "object"}}, ["bl_idname"])},
    {"name": "scene_audit",
     "description": "What the scene really contains compared with the start of this turn: "
                    "objects added, tris, materials, rigs, actions, and problems found "
                    "(floating parts, no material, unweighted vertices, actions that don't "
                    "move, unused armatures). Run it before your final reply.",
     "inputSchema": _schema()},
]


def _call_blender(tool, args):
    with socket.create_connection(("127.0.0.1", PORT), timeout=10) as conn:
        conn.sendall(json.dumps({"token": TOKEN, "tool": tool, "args": args}).encode() + b"\n")
        conn.settimeout(None)  # the script runs as long as it runs
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    if not data:
        raise ConnectionError("Blender closed the connection")
    return json.loads(data)


def _tool_result(name, args):
    try:
        res = _call_blender(name, args)
    except (OSError, ValueError) as e:
        return {"content": [{"type": "text", "text": f"Can't reach Blender: {e}"}],
                "isError": True}
    if "error" in res:
        return {"content": [{"type": "text", "text": res["error"]}], "isError": True}
    if "image" in res:
        content = [{"type": "image", "data": res["image"], "mimeType": "image/png"}]
        if res.get("caption"):
            content.append({"type": "text", "text": res["caption"]})
        return {"content": content}
    return {"content": [{"type": "text", "text": res.get("text", "")}]}


def _handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "blender", "version": "1.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        return _tool_result(params.get("name"), params.get("arguments") or {})
    if method == "ping":
        return {}
    raise LookupError(method)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:
            continue  # a notification: nothing to answer
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": _handle(msg)}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": f"Method not found: {e}"}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

# Credits and licences

## This add-on

**LLM Chat for Blender** — © 2026 raf. Licensed under the **GNU General Public License
v3.0 or later** (see `LICENSE`), as Blender add-ons must be.

Built together with Claude (Anthropic) over a series of sessions: the modelling helper
library, the bake pipeline and the system prompt were designed, written and tested in a
live Blender scene.

## What it needs, and who made it

The add-on talks to Claude through its own small built-in tool server
(`mcp_server.py` and `bridge.py`, part of this add-on and under the same licence). Nothing
else is bundled; the rest comes from each project's own official source:

| Component | Author | Licence | Installed from |
|---|---|---|---|
| **Claude Code CLI** | Anthropic | Proprietary, needs your own account | claude.ai, via the add-on's Install button or your own install |
| **Extra Mesh Objects** (optional) | Blender community | GPL-3.0-or-later | extensions.blender.org |
| **LoopTools** (optional) | Blender community | GPL-2.0-or-later | extensions.blender.org |
| **Tissue** (optional) | Blender community | GPL-2.0-or-later | extensions.blender.org |
| **Rigify** | Blender | GPL-2.0-or-later | ships with Blender |

The Blender tools (running code, scene and object info, viewport screenshots, API and
node lookups) follow the design of Siddharth Ahuja's **MCP for Blender**, which earlier
versions of this add-on used: the same tool set, and its off-screen viewport capture.
They were rewritten for this add-on, and its MIT notice is kept here:

```
MIT License

Copyright (c) 2025 Siddharth Ahuja

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Not affiliated

Claude and Anthropic are trademarks of Anthropic PBC. This is an independent community
add-on: it is not made, endorsed or supported by Anthropic, and it does not include any
Anthropic software. It runs the Claude Code CLI that you install and sign into yourself.

Blender is a registered trademark of the Blender Foundation.

## Your account, your usage

The add-on drives the Claude Code CLI using whatever login that CLI already has. Messages
count against your own Claude subscription or API credit. No keys are stored by this
add-on, and it sends nothing itself: your messages, the scene context and tool results go
to Anthropic through the CLI you installed. Claude runs Python inside your Blender, which
can reach your whole computer; see "Security and privacy" on the add-on's page.

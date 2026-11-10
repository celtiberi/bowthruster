"""MCP Tool & Resource Schema Definitions (`bowthruster/mcp_schemas.py`)."""

from __future__ import annotations

from typing import Any

MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "bowthruster-mcp"
SERVER_VERSION = "0.1.0"

COMPACT_PACKET_KEYS: tuple[str, ...] = (
    "packet_id",
    "wave",
    "component",
    "owner",
    "stage",
    "build_status",
    "files",
    "depends_on",
    "prompt_slice",
    "laws_skeleton",
    "python_skeleton",
    "python_skeletons_by_file",
    "is_skeleton_complete",
)

MCP_RESOURCES: tuple[dict[str, str], ...] = (
    {
        "uri": "bowthruster://readme",
        "name": "BowThruster README & MCP Quickstart",
        "description": (
            "Quickstart guide for AI agents using the BowThruster MCP server "
            "(pipe-table format, workflow, and tools)."
        ),
        "mimeType": "text/markdown",
    },
    {
        "uri": "bowthruster://guide",
        "name": "BowThruster System Architecture & Brownfield Guide",
        "description": (
            "Full system architecture, Bend 2 formal laws, and brownfield repo migration guide."
        ),
        "mimeType": "text/markdown",
    },
)

MCP_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "readme",
        "description": (
            "Read the BowThruster README, System Architecture Guide, or the unified "
            "'bowthruster' SKILL.md template so you know how to format pipe tables "
            "and operate BowThruster."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": [
                        "readme",
                        "full_readme",
                        "guide",
                        "skill",
                        "skills",
                        "change",
                        "navigate",
                        "qa",
                        "all",
                    ],
                    "description": (
                        "Which documentation to return: 'readme' (default inline quickstart "
                        "+ unified skill), 'full_readme' (full README.md), 'guide' (full "
                        "architecture & brownfield guide), 'skill'/'skills' (unified "
                        "bowthruster SKILL.md template), or 'all'."
                    ),
                },
            },
        },
    },
    {
        "name": "atlas",
        "description": (
            "Step 1 (Explore LOD_0 or Living Spec): Render the 1-line-per-component Atlas "
            "topology (with Charter, Role, Why/Rationale, verification statuses, > FLOW edges, "
            "health metrics, and optional Mermaid graph) or pass spec=true to compile the full "
            "human-readable Markdown specification from the live board."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Board name (default: lattice_core)"},
                "boards": {"type": "string", "description": "Comma-separated list of board names"},
                "load_all": {"type": "boolean", "description": "Load all discovered boards"},
                "mermaid": {"type": "boolean", "description": "Include Mermaid flowchart"},
                "spec": {
                    "type": "boolean",
                    "description": (
                        "Render the complete human-readable Markdown specification "
                        "(Charter, Purpose/Why, Mermaid topology, Flows, Interfaces, "
                        "Schemas, Laws, Rules) from the live board"
                    ),
                },
                "project_root": {
                    "type": "string",
                    "description": (
                        "Optional repository root path (auto-discovered by board name "
                        "or file paths if omitted)"
                    ),
                },
            },
        },
    },
    {
        "name": "slice",
        "description": (
            "Step 1 (Slice + Code in One Shot): Retrieve a <2KB component/symbol slice AND "
            "exact #Lstart-Lend source code (set query='<function_or_helper>', include_code=true) "
            "BEFORE calling read_file or grep. Pass mark_stale=true when pulling code for an "
            "internal fix (no contract change) to mark the matched component STALE in one shot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Board name (default: lattice_core)"},
                "component": {"type": "string", "description": "Component scope ID"},
                "division": {"type": "string", "description": "Owner division alias"},
                "stage": {"type": "string", "description": "Stage identifier"},
                "schema": {"type": "string", "description": "Schema or contract name"},
                "kind": {
                    "type": "string",
                    "description": "Artifact kind (COMPONENT, FLOW, INTERFACE, LAW, SCHEMA)",
                },
                "query": {
                    "type": "string",
                    "description": "Keyword or AST function/class/_helper name query",
                },
                "include_code": {
                    "type": "boolean",
                    "description": (
                        "Inline exact #Lstart-Lend source code blocks for matched "
                        "functions/classes/helpers (default: false)"
                    ),
                },
                "mark_stale": {
                    "type": "boolean",
                    "description": (
                        "Transition matched component(s) to STALE in one shot for "
                        "internal code fixes (default: false)"
                    ),
                },
                "include_raw": {
                    "type": "boolean",
                    "description": "Include full raw records (default: false)",
                },
                "project_root": {
                    "type": "string",
                    "description": (
                        "Optional repository root path (auto-discovered by board name "
                        "or file paths if omitted)"
                    ),
                },
            },
        },
    },
    {
        "name": "packets",
        "description": (
            "Step 3 (Build STALE Packet): Compile self-contained WorkPackets with Bend 2 "
            "laws_skeleton, Python AST python_skeleton (with Spec docstrings & # MUST CALL "
            "anchors), and baseline_commit_sha."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Board name (default: lattice_core)"},
                "wave": {"type": "integer", "description": "Filter by wave (0, 1, or 2)"},
                "component": {"type": "string", "description": "Filter by component name"},
                "status": {
                    "type": "string",
                    "description": "Filter by build_status (e.g. STALE, READY, VERIFIED)",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force packet compilation even if board is INCOMPLETE_DRAFT",
                },
                "include_raw": {
                    "type": "boolean",
                    "description": "Include raw artifacts and wave buckets (default: false)",
                },
                "project_root": {
                    "type": "string",
                    "description": (
                        "Optional repository root path (auto-discovered by board name "
                        "or file paths if omitted)"
                    ),
                },
            },
        },
    },
    {
        "name": "turn",
        "description": (
            "Step 2 (Mutate Board First -> STALE): Compile Markdown pipe tables into the "
            "Whiteboard in <50ms BEFORE editing any VERIFIED file when contracts/architecture "
            "change. Canonical table headers: | Charter | Summary | Principles |, "
            "| Component | Role | Why | Owner | Stage | Depends_On | Files |, "
            "| Flow | Source | Target | Payload | Condition |, "
            "| Interface | Component | Inputs | Outputs | Owner | Stage | File | Spec |, "
            "| Law | Component | Owner | Stage | Bend_Dir | Args | LHS | RHS | Spec |, "
            "| Schema | Component | Fields | Owner | Stage | File | Spec |, "
            "| Rule | Component | Type | Property | Op | Value | Condition |."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Target board name"},
                "speaker": {"type": "string", "description": "Speaker alias (default: agent)"},
                "message": {
                    "type": "string",
                    "description": (
                        "Canonical Markdown pipe tables (Charter, Component, Flow, "
                        "Interface, Law, Schema, Rule)"
                    ),
                },
                "round_num": {"type": "integer", "description": "Optional round number"},
                "profile": {
                    "type": "string",
                    "description": "Runtime profile (cloud, hybrid, local)",
                },
                "project_root": {
                    "type": "string",
                    "description": (
                        "Optional repository root path (auto-discovered by board name "
                        "or file paths if omitted)"
                    ),
                },
            },
            "required": ["board", "message"],
        },
    },
    {
        "name": "git_sync",
        "description": (
            "Step 4 (Verify & Sync): Run Git <-> Whiteboard sync: mode='pre' (pre-commit gate "
            "checking STALE edit guard, AST signatures, call-graph % LAW reachability, "
            "> FLOW schemas, dangling imports, Bend 2 proofs, and delta pytest suites; pass "
            "mark_verified=true to auto-transition STALE components to VERIFIED when allowed=true "
            "and delta tests pass), mode='post' (stamp commit_sha, heal git mv renames, refresh "
            "#Lstart-Lend AST anchors, cascade STALE), mode='baseline', or mode='drift'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {
                    "type": "string",
                    "description": "Target board name (default: lattice_core)",
                },
                "mode": {
                    "type": "string",
                    "enum": ["pre", "post", "baseline", "drift"],
                    "description": "Sync mode: pre, post, baseline, or drift",
                },
                "mark_verified": {
                    "type": "boolean",
                    "description": (
                        "Auto-transition STALE components to VERIFIED when mode='pre' "
                        "gate and delta tests pass (default: false)"
                    ),
                },
                "manifest": {
                    "type": "string",
                    "description": "Optional JSON manifest path or inline JSON string",
                },
                "project_root": {
                    "type": "string",
                    "description": (
                        "Optional repository root path (auto-discovered by board name "
                        "or file paths if omitted)"
                    ),
                },
            },
        },
    },
    {
        "name": "status",
        "description": (
            "Step 4 (Transition Status): Transition a component's build lifecycle status "
            "(e.g. new_status='VERIFIED' after pre-commit gate and delta/seam tests pass)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {
                    "type": "string",
                    "description": "Target board name (default: lattice_core)",
                },
                "component": {"type": "string", "description": "Component scope ID"},
                "new_status": {
                    "type": "string",
                    "description": (
                        "Target status (READY, IN_PROGRESS, IN_QA, VERIFIED, FAILED, STALE)"
                    ),
                },
                "build_tag": {"type": "string", "description": "Optional build tag"},
                "core_tag": {"type": "string", "description": "Optional core tag"},
                "reason": {"type": "string", "description": "Optional failure diagnostic"},
                "project_root": {
                    "type": "string",
                    "description": (
                        "Optional repository root path (auto-discovered by board name "
                        "or file paths if omitted)"
                    ),
                },
            },
            "required": ["component", "new_status"],
        },
    },
    {
        "name": "logs",
        "description": (
            "Query the append-only Structured Flight Recorder "
            "(.whiteboard/logs/bowthruster_events.jsonl) to diagnose rejected turns or gate errors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "board": {"type": "string", "description": "Optional board name filter"},
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default: 20)",
                },
                "status": {
                    "type": "string",
                    "description": "Optional status filter (OK, REJECTED, ERROR)",
                },
                "project_root": {
                    "type": "string",
                    "description": (
                        "Optional repository root path (auto-discovered by board name "
                        "or file paths if omitted)"
                    ),
                },
            },
        },
    },
)

KNOWN_TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in MCP_TOOLS)

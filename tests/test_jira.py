from __future__ import annotations

from typing import Any

import httpx
import pytest

from gemini_orchestrator.demo import demo_tickets
from gemini_orchestrator.engine import Engine
from gemini_orchestrator.jira import (
    MCPJira,
    MCPTransport,
    RestJira,
    filter_tickets,
    normalize_issue,
    quote_jql,
    sprint_jql,
)
from gemini_orchestrator.models import OrchestratorError


def issue(
    key: str = "DEMO-1", assignee: str = "demo-alex", completed: bool = False
) -> dict[str, Any]:
    return {
        "key": key,
        "fields": {
            "summary": "Synthetic issue",
            "assignee": {"accountId": assignee},
            "project": {"id": "100"},
            "description": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Acceptance criteria"}],
                    }
                ],
            },
            "status": {
                "name": "Done" if completed else "To Do",
                "statusCategory": {"key": "done" if completed else "new"},
            },
            "issuelinks": [
                {
                    "type": {"name": "Blocks", "inward": "is blocked by"},
                    "inwardIssue": {"key": "DEMO-9"},
                }
            ],
        },
    }


class FakeMCP:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tools = [
            {
                "name": "jira_get_current_user",
                "annotations": {"readOnlyHint": True, "destructiveHint": False},
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "jira_search",
                "annotations": {"readOnlyHint": True, "destructiveHint": False},
                "inputSchema": {
                    "type": "object",
                    "required": ["jql"],
                    "properties": {
                        "jql": {"type": "string"},
                        "start_at": {"type": "integer"},
                        "max_results": {"type": "integer"},
                    },
                },
            },
            {
                "name": "jira_delete_issue",
                "annotations": {"readOnlyHint": True, "destructiveHint": False},
                "inputSchema": {"type": "object"},
            },
        ]
        self.account = "demo-alex"

    async def list_tools(self) -> list[dict[str, Any]]:
        return self.tools

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if name == "jira_get_current_user":
            return {"accountId": self.account, "displayName": "Alex"}
        offset = arguments.get("start_at", 0)
        if offset == 0:
            return {"issues": [issue("DEMO-1"), issue("DEMO-2", "other")], "total": 3}
        return {"issues": [issue("DEMO-3")], "total": 3}


async def test_mcp_capabilities_pagination_identity_and_filtering(engine: Engine) -> None:
    transport = FakeMCP()
    adapter = MCPJira(transport)
    capabilities = await adapter.discover()
    assert capabilities == {"identity": "jira_get_current_user", "search": "jira_search"}
    tickets = await adapter.issues(engine.profile)
    assert [ticket.key for ticket in tickets] == ["DEMO-1", "DEMO-3"]
    assert tickets[0].dependencies == ["DEMO-9"]
    assert "Acceptance" in tickets[0].description
    assert all(name != "jira_delete_issue" for name, _ in transport.calls)
    assert [args["start_at"] for name, args in transport.calls if name == "jira_search"] == [0, 50]
    with pytest.raises(OrchestratorError):
        await adapter.read("delete", issue_key="DEMO-1")


@pytest.mark.parametrize(
    "annotations",
    [
        {"readOnlyHint": False, "destructiveHint": False},
        {"readOnlyHint": True, "destructiveHint": True},
        {"readOnlyHint": "true"},
        {"destructiveHint": 0},
        "invalid",
    ],
)
async def test_mcp_explicit_unsafe_or_malformed_annotations_disabled(annotations: Any) -> None:
    transport = FakeMCP()
    transport.tools[0]["annotations"] = annotations
    adapter = MCPJira(transport)
    with pytest.raises(OrchestratorError, match="disabled"):
        await adapter.discover()
    assert not transport.calls


@pytest.mark.parametrize(
    "annotations", [None, {}, {"readOnlyHint": True}, {"destructiveHint": False}]
)
async def test_known_read_tools_allow_missing_annotation_hints(annotations: Any) -> None:
    transport = FakeMCP()
    for tool in transport.tools:
        tool["annotations"] = annotations
    adapter = MCPJira(transport)
    assert await adapter.discover() == {
        "identity": "jira_get_current_user",
        "search": "jira_search",
    }
    assert "jira_delete_issue" not in adapter.tools
    assert (await adapter.identity()).account_id == "demo-alex"


@pytest.mark.parametrize("field", ["accountId", "account_id", "name", "key"])
@pytest.mark.parametrize("as_list", [True, False])
async def test_server_identity_search_aliases_and_payloads(
    engine: Engine, field: str, as_list: bool
) -> None:
    class ServerMCP(FakeMCP):
        async def call(self, name: str, arguments: dict[str, Any]) -> Any:
            self.calls.append((name, arguments))
            if name == "get_current_user":
                return {"accountId": "", field: "demo-alex", "displayName": "Alex"}
            if name == "jira_get_projects":
                return [{"id": "100", "name": "Synthetic"}]
            assert name == "jira_search_issues"
            assert arguments["jql"] == 'assignee = "demo-alex" AND sprint = 101'
            items = [issue(), issue("DEMO-2", "other"), issue("DEMO-3", completed=True)]
            for item in items:
                item["fields"]["assignee"] = {field: item["fields"]["assignee"]["accountId"]}
            return items if as_list else {"issues": items}

    transport = ServerMCP()
    transport.tools[0]["name"] = "get_current_user"
    transport.tools[1]["name"] = "jira_search_issues"
    transport.tools.append(
        {"name": "jira_get_projects", "inputSchema": {"type": "object", "properties": {}}}
    )
    for tool in transport.tools:
        tool.pop("annotations", None)
    adapter = MCPJira(transport)
    await adapter.discover()
    assert (await adapter.identity()).account_id == "demo-alex"
    assert (await adapter.projects())[0].id == "100"
    assert [ticket.key for ticket in await adapter.issues(engine.profile)] == ["DEMO-1"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("123", "123"),
        ("00123", "123"),
        ("Sprint 1", '"Sprint 1"'),
        ('123" OR sprint != "', '"123\\" OR sprint != \\""'),
    ],
)
def test_sprint_literals_are_numeric_or_safely_quoted(value: str, expected: str) -> None:
    assert sprint_jql(value) == expected


@pytest.mark.parametrize("offset_supported", [True, False])
async def test_list_search_cannot_silently_repeat_or_truncate(
    engine: Engine, offset_supported: bool
) -> None:
    class RepeatedPage(FakeMCP):
        async def call(self, name: str, arguments: dict[str, Any]) -> Any:
            if name == "jira_get_current_user":
                return {"name": "demo-alex"}
            return [issue(f"DEMO-{i}") for i in range(1, 51)]

    transport = RepeatedPage()
    if not offset_supported:
        transport.tools[1]["inputSchema"]["properties"].pop("start_at")
    adapter = MCPJira(transport)
    await adapter.discover()
    with pytest.raises(OrchestratorError, match="repeated|complete pagination"):
        await adapter.issues(engine.profile)


@pytest.mark.parametrize("payload", ["not JSON data", {"issues": {}}, ["invalid issue"]])
async def test_malformed_search_response_rejected(engine: Engine, payload: Any) -> None:
    class Malformed(FakeMCP):
        async def call(self, name: str, arguments: dict[str, Any]) -> Any:
            return {"name": "demo-alex"} if name == "jira_get_current_user" else payload

    adapter = MCPJira(Malformed())
    await adapter.discover()
    with pytest.raises(OrchestratorError, match="search"):
        await adapter.issues(engine.profile)


async def test_mcp_mismatched_identity_never_searches(engine: Engine) -> None:
    transport = FakeMCP()
    transport.account = "different-user"
    adapter = MCPJira(transport)
    await adapter.discover()
    with pytest.raises(OrchestratorError, match="differs"):
        await adapter.issues(engine.profile)
    assert all(name != "jira_search" for name, _ in transport.calls)


async def test_mcp_unknown_required_schema_fails_closed() -> None:
    transport = FakeMCP()
    transport.tools[0]["inputSchema"] = {"type": "object", "required": ["unknownRequired"]}
    adapter = MCPJira(transport)
    await adapter.discover()
    with pytest.raises(OrchestratorError, match="schema"):
        await adapter.identity()
    assert not transport.calls


async def test_rest_get_only_token_pagination_and_filtering(engine: Engine) -> None:
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("myself"):
            return httpx.Response(200, json={"accountId": "demo-alex", "displayName": "Alex"})
        if request.url.path.endswith("sprint"):
            return httpx.Response(
                200, json={"values": [{"id": 101, "name": "Sprint"}], "isLast": True}
            )
        if "nextPageToken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "issues": [issue(), issue("DEMO-2", "other"), issue("DEMO-3", completed=True)],
                    "nextPageToken": "second",
                },
            )
        return httpx.Response(200, json={"issues": [issue("DEMO-4")]})

    profile = engine.profile.model_copy(deep=True)
    profile.jira.base_url = "https://example.invalid"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond), base_url=profile.jira.base_url
    ) as client:
        adapter = RestJira(profile, client)
        tickets = await adapter.issues(profile)
        assert [ticket.key for ticket in tickets] == ["DEMO-1", "DEMO-4"]
        with pytest.raises(OrchestratorError, match="allowlist"):
            await adapter.get("/rest/api/3/issue/DEMO-1/transitions")
        with pytest.raises(OrchestratorError):
            await adapter.get("https://evil.invalid")
    assert len(calls) == 4


async def test_rest_permission_and_redirect_errors(engine: Engine) -> None:
    profile = engine.profile.model_copy(deep=True)
    profile.jira.base_url = "https://example.invalid"
    for status in (401, 403, 302):
        async with httpx.AsyncClient(
            base_url=profile.jira.base_url,
            transport=httpx.MockTransport(lambda _, code=status: httpx.Response(code)),
        ) as client:
            with pytest.raises(OrchestratorError):
                await RestJira(profile, client).identity()


async def test_rest_repeated_token_aborts(engine: Engine) -> None:
    profile = engine.profile.model_copy(deep=True)
    profile.jira.base_url = "https://example.invalid"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("myself"):
            return httpx.Response(200, json={"accountId": "demo-alex"})
        if request.url.path.endswith("sprint"):
            return httpx.Response(
                200, json={"values": [{"id": 101, "name": "Sprint"}], "isLast": True}
            )
        return httpx.Response(200, json={"issues": [issue()], "nextPageToken": "repeated"})

    async with httpx.AsyncClient(
        base_url=profile.jira.base_url, transport=httpx.MockTransport(respond)
    ) as client:
        with pytest.raises(OrchestratorError, match="repeated"):
            await RestJira(profile, client).issues(profile)


def test_filters_exact_assignee_sprint_project_and_done(engine: Engine) -> None:
    tickets = demo_tickets()
    tickets[0].assignee = "other"
    tickets[1].sprint_id = "other"
    tickets[2].completed = True
    tickets[3].project_id = "other"
    assert [ticket.key for ticket in filter_tickets(tickets, engine.profile)] == [
        "DEMO-5",
        "DEMO-6",
    ]


def test_jql_data_cannot_escape_string() -> None:
    assert quote_jql('user" OR assignee != "') == '"user\\" OR assignee != \\""'


def test_normalization_preserves_links_parent_and_acceptance() -> None:
    raw = issue()
    raw["fields"]["customfield_123"] = "Ship acceptance"
    raw["fields"]["parent"] = {"key": "DEMO-7"}
    raw["fields"]["comment"] = {"comments": [{"body": "Relevant comment"}]}
    ticket = normalize_issue(raw, "101", "customfield_123")
    assert ticket.acceptance == ["Ship acceptance"]
    assert ticket.parent == "DEMO-7"
    assert ticket.comments == ["Relevant comment"]


async def test_real_local_stdio_mcp_transport(engine: Engine, tmp_path) -> None:
    import json
    import sys

    from gemini_orchestrator.jira import JiraConnection

    server = tmp_path / "synthetic_mcp.py"
    server.write_text("""from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
server = FastMCP("Synthetic Jira")
readonly = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
@server.tool(annotations=readonly)
def jira_get_current_user() -> dict:
    return {"accountId": "demo-alex", "displayName": "Synthetic Alex"}
@server.tool(annotations=readonly)
def jira_search(jql: str, start_at: int = 0, max_results: int = 50) -> dict:
    return {"issues": [{"key": "DEMO-1", "fields": {"summary": "Synthetic MCP issue", "assignee": {"accountId": "demo-alex"}, "project": {"id": "100"}, "status": {"name": "To Do"}}}], "total": 1}
server.run(transport="stdio")
""")
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "synthetic-jira": {
                        "command": sys.executable,
                        "args": [str(server)],
                        "includeTools": ["jira_get_current_user", "jira_search"],
                    }
                }
            }
        )
    )
    profile = engine.profile.model_copy(deep=True)
    profile.mode = "connected"
    profile.jira.settings_path = settings
    profile.jira.server = "synthetic-jira"
    async with JiraConnection(profile) as jira:
        assert (await jira.identity()).account_id == "demo-alex"
        tickets = await jira.issues(profile)
        assert len(tickets) == 1
        assert tickets[0].summary == "Synthetic MCP issue"


async def test_legacy_server_over_real_stdio_with_tool_filters(engine: Engine, tmp_path) -> None:
    import json
    import sys

    from gemini_orchestrator.jira import JiraConnection

    server = tmp_path / "legacy_mcp.py"
    server.write_text("""import json
from mcp.server.fastmcp import FastMCP
server = FastMCP("Synthetic legacy Jira")
@server.tool()
def get_current_user() -> dict:
    return {"name": "demo-alex", "displayName": "Synthetic Alex"}
@server.tool(structured_output=False)
def jira_search_issues(jql: str, start_at: int = 0, max_results: int = 50) -> str:
    assert jql == 'assignee = "demo-alex" AND sprint = 101'
    return json.dumps([{"key": "DEMO-1", "fields": {"summary": "Legacy list result", "assignee": {"name": "demo-alex"}, "project": {"id": "100"}}}])
@server.tool()
def jira_delete_issue() -> dict:
    raise AssertionError("Unknown tools must never run")
@server.tool()
def jira_get_projects() -> dict:
    raise AssertionError("Excluded tools must never run")
server.run(transport="stdio")
""")
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "legacy": {
                        "command": sys.executable,
                        "args": [str(server)],
                        "includeTools": [
                            "get_current_user",
                            "jira_search_issues",
                            "jira_delete_issue",
                            "jira_get_projects",
                        ],
                        "excludeTools": ["jira_get_projects"],
                    }
                }
            }
        )
    )
    profile = engine.profile.model_copy(deep=True)
    profile.mode = "connected"
    profile.jira.settings_path = settings
    profile.jira.server = "legacy"
    async with JiraConnection(profile) as jira:
        assert isinstance(jira, MCPJira)
        assert set(jira.operations) == {"identity", "search"}
        assert (await jira.issues(profile))[0].summary == "Legacy list result"
    # includeTools narrows the catalog and cannot bypass explicit unsafe hints.
    server.write_text(
        server.read_text().replace(
            "@server.tool()\ndef get_current_user",
            '@server.tool(annotations={"readOnlyHint": False})\ndef get_current_user',
        )
    )
    with pytest.raises(OrchestratorError, match="disabled"):
        async with JiraConnection(profile):
            pytest.fail("Explicit unsafe hints must not be overridden by includeTools")


async def test_blank_mcp_server_rejected_before_launch(engine: Engine, tmp_path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"mcpServers": {"": {"command": "must-not-run"}}}')
    engine.profile.jira.settings_path = settings
    engine.profile.jira.server = " "
    with pytest.raises(OrchestratorError, match="non-empty"):
        await MCPTransport(engine.profile).connect()

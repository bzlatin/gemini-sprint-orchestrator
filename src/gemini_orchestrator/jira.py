from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import AsyncExitStack
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from .models import Identity, OrchestratorError, Profile, Resource, Ticket


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text(item) for item in value)
    if isinstance(value, dict):
        return str(value.get("text", "")) + text(value.get("content", []))
    return str(value)


def quote_jql(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def account_identifier(raw: dict[str, Any]) -> str:
    """Same canonical identity fields for authenticated users and issue assignees."""
    return next(
        (str(raw[key]) for key in ("accountId", "account_id", "name", "key") if raw.get(key)), ""
    )


def sprint_jql(value: str) -> str:
    # Numeric IDs are literals; names remain quoted and escaped as data.
    return str(int(value)) if re.fullmatch(r"[0-9]+", value) else quote_jql(value)


def normalize_issue(raw: dict[str, Any], sprint: str, acceptance_field: str = "") -> Ticket:
    fields = raw.get("fields", raw)
    links = fields.get("issuelinks", [])
    dependencies = [
        link["inwardIssue"]["key"]
        for link in links
        if "inwardIssue" in link and "block" in link.get("type", {}).get("inward", "").lower()
    ]
    status = fields.get("status", {})
    assignee = fields.get("assignee") or {}
    comments = fields.get("comment", {}).get("comments", [])
    return Ticket(
        key=raw["key"],
        summary=fields["summary"],
        description=text(fields.get("description")),
        acceptance=[text(fields[acceptance_field])]
        if acceptance_field and fields.get(acceptance_field)
        else [],
        assignee=account_identifier(assignee),
        sprint_id=sprint,
        project_id=str(fields.get("project", {}).get("id", "")),
        priority=(fields.get("priority") or {}).get("name", "Medium"),
        status=status.get("name", "Unknown"),
        completed=status.get("statusCategory", {}).get("key") == "done",
        issue_type=fields.get("issuetype", {}).get("name", "Task"),
        parent=fields.get("parent", {}).get("key"),
        dependencies=dependencies,
        comments=[text(c.get("body")) for c in comments],
        links=[
            {
                "type": link.get("type", {}).get("name", ""),
                "key": (link.get("inwardIssue") or link.get("outwardIssue") or {}).get("key", ""),
            }
            for link in links
        ],
    )


class Jira(Protocol):
    async def identity(self) -> Identity: ...
    async def projects(self) -> list[Resource]: ...
    async def boards(self) -> list[Resource]: ...
    async def sprints(self, board_id: str) -> list[Resource]: ...
    async def issues(self, profile: Profile) -> list[Ticket]: ...


async def validate_identity(adapter: Jira, profile: Profile) -> Identity:
    identity = await adapter.identity()
    if not identity.account_id or not profile.identity.account_id:
        raise OrchestratorError("Verify and confirm your Jira account identifier in setup")
    if identity.account_id != profile.identity.account_id:
        raise OrchestratorError(
            "Authenticated Jira identity differs from this profile; re-run setup, never substitute another user's tickets"
        )
    return identity


def validate_scope(profile: Profile) -> None:
    if not profile.board_id or profile.board_id not in {board.id for board in profile.boards}:
        raise OrchestratorError("Choose a stable board ID in setup")
    if not profile.sprint or profile.sprint.board_id != profile.board_id:
        raise OrchestratorError("Choose and confirm a sprint belonging to the selected board")


def filter_tickets(tickets: list[Ticket], profile: Profile) -> list[Ticket]:
    validate_scope(profile)
    assert profile.sprint
    scoped = [
        ticket
        for ticket in tickets
        if ticket.assignee == profile.identity.account_id
        and ticket.sprint_id == profile.sprint.id
        and (profile.jira.include_completed or not ticket.completed)
    ]
    projects = {project.id for project in profile.projects}
    return [ticket for ticket in scoped if not projects or ticket.project_id in projects]


class RestJira:
    """Jira Cloud GET-only client. Authentication is read from environment, never saved."""

    def __init__(self, profile: Profile, client: httpx.AsyncClient | None = None):
        self.profile = profile
        url = urlparse(profile.jira.base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise OrchestratorError(
                "Jira base URL must be HTTPS without credentials, query or fragment"
            )
        self.owns_client = client is None
        token = os.environ.get(profile.jira.token_env, "")
        email = os.environ.get(profile.jira.email_env, "")
        if client is None and not token:
            raise OrchestratorError(
                f"Set {profile.jira.token_env} using your approved credential mechanism"
            )
        self.client = client or httpx.AsyncClient(
            base_url=profile.jira.base_url.rstrip("/"),
            auth=httpx.BasicAuth(email, token) if email else None,
            headers={} if email else {"Authorization": f"Bearer {token}"},
            timeout=30,
            follow_redirects=False,
        )

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        # No arbitrary URL or method is accepted at the boundary.
        if not re.fullmatch(
            r"/rest/(api/3/(myself|project/search|search/jql|issue/[A-Z][A-Z0-9_]*-\d+)|agile/1\.0/(board|board/\d+/sprint))",
            path,
        ):
            raise OrchestratorError("Jira operation is outside the read-only allowlist")
        for attempt in range(3):
            response = await self.client.get(path, params=params)
            if response.status_code in {429, 502, 503} and attempt < 2:
                delay = response.headers.get("Retry-After", "1")
                await asyncio.sleep(min(float(delay) if delay.isdigit() else 1, 5))
                continue
            if response.is_redirect:
                raise OrchestratorError("Jira redirected; configure the final approved endpoint")
            if response.is_error:
                raise OrchestratorError(
                    f"Jira GET failed ({response.status_code}); check permissions, URL and authentication"
                )
            return dict(response.json())
        raise OrchestratorError("Jira temporarily unavailable")

    async def identity(self) -> Identity:
        raw = await self.get("/rest/api/3/myself")
        return Identity(
            account_id=str(raw.get("accountId", "")),
            display_name=raw.get("displayName", ""),
            email=raw.get("emailAddress"),
        )

    async def collection(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1000):
            data = await self.get(path, {**(params or {}), "startAt": page * 50, "maxResults": 50})
            values = data.get("values", [])
            result.extend(values)
            if (
                data.get("isLast", False)
                or not values
                or len(result) >= data.get("total", float("inf"))
            ):
                return result
        raise OrchestratorError("Pagination exceeded safety limit; narrow the project scope")

    async def projects(self) -> list[Resource]:
        return [
            Resource(id=str(raw["id"]), name=raw["name"])
            for raw in await self.collection("/rest/api/3/project/search")
        ]

    async def boards(self) -> list[Resource]:
        return [
            Resource(id=str(raw["id"]), name=raw["name"])
            for raw in await self.collection("/rest/agile/1.0/board")
        ]

    async def sprints(self, board_id: str) -> list[Resource]:
        if not board_id.isdigit():
            raise OrchestratorError("REST board IDs must be numeric")
        return [
            Resource(id=str(raw["id"]), name=raw["name"], board_id=board_id)
            for raw in await self.collection(
                f"/rest/agile/1.0/board/{board_id}/sprint", {"state": "active"}
            )
        ]

    async def issues(self, profile: Profile) -> list[Ticket]:
        identity = await validate_identity(self, profile)
        validate_scope(profile)
        assert profile.sprint
        available = await self.sprints(profile.board_id)
        if profile.sprint_behavior == "active" and profile.sprint.id not in {
            s.id for s in available
        }:
            raise OrchestratorError(
                "Configured sprint is no longer active; select the correct sprint in profile edit"
            )
        jql = f"assignee = {quote_jql(identity.account_id)} AND sprint = {sprint_jql(profile.sprint.id)}"
        result: list[Ticket] = []
        token = ""
        seen: set[str] = set()
        for _ in range(1000):
            params = {"jql": jql, "maxResults": "100", "fields": "*all"}
            if token:
                params["nextPageToken"] = token
            raw = await self.get("/rest/api/3/search/jql", params)
            result.extend(
                normalize_issue(issue, profile.sprint.id, profile.jira.acceptance_field)
                for issue in raw.get("issues", [])
            )
            token = raw.get("nextPageToken", "")
            if not token:
                return filter_tickets(result, profile)
            if token in seen:
                raise OrchestratorError("Jira repeated a pagination token")
            seen.add(token)
        raise OrchestratorError("Jira pagination safety limit reached")


# Exact known read operations. Missing annotations are accepted for these names only;
# explicit unsafe hints, unknown names and excluded tools remain blocked.
OPERATIONS: dict[str, tuple[str, ...]] = {
    "identity": ("jira_get_current_user", "get_current_user", "atlassianUserInfo"),
    "projects": ("jira_get_all_projects", "getVisibleJiraProjects", "jira_get_projects"),
    "boards": ("jira_get_agile_boards", "getJiraBoards"),
    "sprints": ("jira_get_sprints_from_board", "getJiraSprints"),
    "search": ("jira_search", "searchJiraIssuesUsingJql", "jira_search_issues"),
    "issue": ("jira_get_issue", "getJiraIssue"),
}


class ReadTransport(Protocol):
    async def list_tools(self) -> list[dict[str, Any]]: ...
    async def call(self, name: str, arguments: dict[str, Any]) -> Any: ...


def expand_env(value: str) -> str:
    expanded = os.path.expandvars(value)
    if re.search(r"\$[A-Za-z_{]", expanded):
        raise OrchestratorError("MCP setting references an unset environment variable")
    return expanded


class MCPTransport:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def connect(self) -> None:
        try:
            settings = json.loads(self.profile.jira.settings_path.expanduser().read_text())
        except (OSError, ValueError) as exc:
            raise OrchestratorError(
                "Cannot read Gemini settings; choose the correct settings_path"
            ) from exc
        if not self.profile.jira.server.strip():
            raise OrchestratorError(
                "Choose a non-empty exact Jira MCP server name from the selected settings file"
            )
        cfg = settings.get("mcpServers", {}).get(self.profile.jira.server)
        if not cfg:
            raise OrchestratorError(
                "Selected MCP server is not in this settings file. Extension-only/OAuth managed connections need an exported compatible configuration or REST fallback"
            )
        global_mcp = settings.get("mcp", {})
        server = self.profile.jira.server
        if server in global_mcp.get("excluded", []) or (
            global_mcp.get("allowed") is not None and server not in global_mcp["allowed"]
        ):
            raise OrchestratorError("Gemini settings deny the selected MCP server")
        self.include = cfg.get("includeTools")
        self.exclude = cfg.get("excludeTools", [])
        if cfg.get("authProviderType") or cfg.get("oauth"):
            raise OrchestratorError(
                "Gemini-managed OAuth cannot be safely replayed by this adapter; use an approved MCP stdio credential helper or REST environment authentication"
            )
        try:
            if cfg.get("httpUrl") or cfg.get("url"):
                url = expand_env(cfg.get("httpUrl") or cfg["url"])
                parsed = urlparse(url)
                if parsed.scheme != "https" or parsed.username or parsed.password:
                    raise OrchestratorError("MCP requires HTTPS without URL credentials")
                headers = {key: expand_env(value) for key, value in cfg.get("headers", {}).items()}
                transport = (
                    streamablehttp_client(url, headers=headers)
                    if cfg.get("httpUrl")
                    else sse_client(url, headers=headers)
                )
                streams = await self.stack.enter_async_context(transport)
                read, write = streams[:2]
            elif cfg.get("command"):
                params = StdioServerParameters(
                    command=expand_env(cfg["command"]),
                    args=[expand_env(arg) for arg in cfg.get("args", [])],
                    env={key: expand_env(value) for key, value in cfg.get("env", {}).items()},
                    cwd=cfg.get("cwd"),
                )
                # Stdio executable is a user-configured trusted integration, never ticket-supplied.
                read, write = await self.stack.enter_async_context(stdio_client(params))
            else:
                raise OrchestratorError("Unsupported MCP server transport")
            self.session = await self.stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
        except BaseException:
            await self.stack.aclose()
            raise

    async def close(self) -> None:
        await self.stack.aclose()

    async def list_tools(self) -> list[dict[str, Any]]:
        if self.session is None:
            raise OrchestratorError("MCP session is not connected")
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(100):
            response = await self.session.list_tools(cursor=cursor)
            tools.extend(
                tool.model_dump(by_alias=True)
                for tool in response.tools
                if (self.include is None or tool.name in self.include)
                and tool.name not in self.exclude
            )
            cursor = response.nextCursor
            if not cursor:
                return tools
        raise OrchestratorError("MCP tool pagination safety limit reached")

    async def call(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.session is None:
            raise OrchestratorError("MCP session is not connected")
        response = await self.session.call_tool(name, arguments)
        if response.isError:
            raise OrchestratorError(
                f"Read-only MCP tool {name} failed; check server logs on your approved system"
            )
        if response.structuredContent:
            return response.structuredContent
        chunks = [content.text for content in response.content if content.type == "text"]
        try:
            return json.loads("\n".join(chunks))
        except ValueError as exc:
            raise OrchestratorError(
                f"{name} did not return structured JSON; this server needs a compatibility adapter"
            ) from exc


class MCPJira:
    def __init__(self, transport: ReadTransport, cloud_id: str = ""):
        self.transport = transport
        self.cloud_id = cloud_id
        self.tools: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, str] = {}

    async def discover(self) -> dict[str, str]:
        raw = await self.transport.list_tools()
        self.tools = {}
        known_names = {name for names in OPERATIONS.values() for name in names}
        for tool in raw:
            annotations = tool.get("annotations")
            if annotations is None:
                annotations = {}
            if not isinstance(annotations, dict):
                continue
            readonly = annotations.get("readOnlyHint")
            destructive = annotations.get("destructiveHint")
            if (
                tool.get("name") in known_names
                and (readonly is None or readonly is True)
                and (destructive is None or destructive is False)
            ):
                self.tools[tool["name"]] = tool
        self.operations = {
            operation: next(name for name in names if name in self.tools)
            for operation, names in OPERATIONS.items()
            if any(name in self.tools for name in names)
        }
        if not {"identity", "search"} <= self.operations.keys():
            raise OrchestratorError(
                "MCP access disabled: identity and search need exact known read-only names and no explicit unsafe annotations. Unknown, excluded and destructive tools are not permitted"
            )
        return self.operations

    async def read(self, operation: str, **values: Any) -> Any:
        if operation not in self.operations:
            raise OrchestratorError(
                f"MCP server does not support read operation {operation}; enter stable IDs manually or use REST"
            )
        name = self.operations[operation]
        schema = self.tools[name].get("inputSchema", {"type": "object"})
        if operation == "search" and "jql" in values and not isinstance(values["jql"], str):
            raise OrchestratorError("JQL must be text")
        properties = schema.get("properties", {})
        aliases = {
            "board_id": "boardId",
            "sprint_id": "sprintId",
            "start_at": "startAt",
            "max_results": "maxResults",
            "issue_key": "issueIdOrKey",
        }
        args: dict[str, Any] = {}
        for key, value in {"cloudId": self.cloud_id, **values}.items():
            target = key if key in properties else aliases.get(key, key)
            if target in properties:
                args[target] = value
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as exc:
            raise OrchestratorError(
                f"Unsupported {name} schema at {list(exc.path)}; configure cloud_id or a server compatibility adapter"
            ) from exc
        # Tools/call is never exposed to model reasoning or ticket content.
        return await self.transport.call(name, args)

    async def identity(self) -> Identity:
        raw = await self.read("identity")
        if not isinstance(raw, dict):
            raise OrchestratorError("Jira identity must be a structured user object")
        return Identity(
            account_id=account_identifier(raw),
            display_name=str(raw.get("displayName") or raw.get("name") or ""),
            email=raw.get("emailAddress", raw.get("email")),
        )

    async def resources(self, operation: str, **values: Any) -> list[Resource]:
        result: list[Resource] = []
        for page in range(1000):
            raw = await self.read(operation, start_at=page * 50, max_results=50, **values)
            items = raw if isinstance(raw, list) else raw.get("values", raw.get("projects", []))
            result.extend(
                Resource(id=str(item["id"]), name=item["name"], board_id=values.get("board_id"))
                for item in items
            )
            if (
                isinstance(raw, list)
                or not items
                or raw.get("isLast", False)
                or len(result) >= raw.get("total", len(result))
            ):
                return result
        raise OrchestratorError("MCP resource pagination limit reached")

    async def projects(self) -> list[Resource]:
        return await self.resources("projects")

    async def boards(self) -> list[Resource]:
        return await self.resources("boards")

    async def sprints(self, board_id: str) -> list[Resource]:
        return await self.resources("sprints", board_id=board_id, state="active")

    async def issues(self, profile: Profile) -> list[Ticket]:
        identity = await validate_identity(self, profile)
        validate_scope(profile)
        assert profile.sprint
        if "sprints" in self.operations and profile.sprint_behavior == "active":
            if profile.sprint.id not in {s.id for s in await self.sprints(profile.board_id)}:
                raise OrchestratorError(
                    "Sprint is no longer active; refresh and confirm sprint selection"
                )
        jql = f"assignee = {quote_jql(identity.account_id)} AND sprint = {sprint_jql(profile.sprint.id)}"
        result: list[Ticket] = []
        token = ""
        seen: set[str] = set()
        previous_pages: set[str] = set()
        for page in range(1000):
            raw = await self.read(
                "search",
                jql=jql,
                start_at=page * 50,
                max_results=50,
                limit=50,
                nextPageToken=token,
                fields="*all",
            )
            if not isinstance(raw, (dict, list)):
                raise OrchestratorError("Jira search must return an issues object or a list")
            items = raw if isinstance(raw, list) else raw.get("issues", [])
            if not isinstance(items, list) or any(
                not isinstance(item, dict) or "key" not in item for item in items
            ):
                raise OrchestratorError("Jira search returned an invalid issue list")
            marker = json.dumps([item["key"] for item in items])
            if items and marker in previous_pages:
                raise OrchestratorError("MCP search repeated a page; refusing incomplete discovery")
            previous_pages.add(marker)
            for item in items:
                if "issue" in self.operations:
                    item = await self.read("issue", issue_key=item["key"], fields="*all")
                result.append(
                    normalize_issue(item, profile.sprint.id, profile.jira.acceptance_field)
                )
            if isinstance(raw, list):
                if len(items) < 50:
                    return filter_tickets(result, profile)
                properties = (
                    self.tools[self.operations["search"]]
                    .get("inputSchema", {})
                    .get("properties", {})
                )
                if not {"startAt", "start_at"} & properties.keys():
                    raise OrchestratorError(
                        "MCP list response cannot establish complete pagination; narrow scope or use a compatible paginated server"
                    )
                continue
            next_token = raw.get("nextPageToken", "")
            if next_token:
                if next_token in seen:
                    raise OrchestratorError("MCP pagination token repeated")
                seen.add(next_token)
                token = next_token
            elif (
                not items
                or raw.get("isLast", False)
                or len(result) >= raw.get("total", len(result))
            ):
                return filter_tickets(result, profile)
            if (
                page
                and "startAt"
                not in self.tools[self.operations["search"]]
                .get("inputSchema", {})
                .get("properties", {})
                and "start_at"
                not in self.tools[self.operations["search"]]
                .get("inputSchema", {})
                .get("properties", {})
                and not token
            ):
                raise OrchestratorError(
                    "MCP server cannot paginate this result; narrow scope or use REST"
                )
        raise OrchestratorError("MCP issue pagination safety limit reached")


class JiraConnection:
    def __init__(self, profile: Profile):
        self.profile = profile
        self.transport: MCPTransport | None = None
        self.rest: RestJira | None = None

    async def __aenter__(self) -> Jira:
        if self.profile.mode != "connected":
            raise OrchestratorError("Demo profiles cannot open enterprise adapters")
        if self.profile.jira.adapter == "rest":
            self.rest = RestJira(self.profile)
            return self.rest
        self.transport = MCPTransport(self.profile)
        await self.transport.connect()
        adapter = MCPJira(self.transport, self.profile.jira.cloud_id)
        try:
            await adapter.discover()
        except BaseException:
            await self.transport.close()
            raise
        return adapter

    async def __aexit__(self, *_: Any) -> None:
        if self.transport:
            await self.transport.close()
        if self.rest:
            await self.rest.close()

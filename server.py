import os
import json
import base64
from typing import Literal, Optional

from dotenv import load_dotenv
from github import Github, GithubException
from mcp.server.fastmcp import FastMCP
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# -----------------------------
# Helpers
# -----------------------------
def _err_msg(e: GithubException) -> str:
    return e.data.get("message") if getattr(e, "data", None) else str(e)


# -----------------------------
# Load environment (GITHUB_TOKEN)
# -----------------------------
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    # Avoid noisy output on stdio (matches your TS version’s behavior)
    raise SystemExit(1)

# GitHub client (PyGithub)
gh = Github(GITHUB_TOKEN)

PORT = os.getenv("PORT", 8000)

# -----------------------------
# MCP server (FastMCP)
# -----------------------------
mcp = FastMCP(
    name="github-mcp-server",
    instructions=(
        "A GitHub MCP server that provides tools to interact with GitHub repositories.\n"
        "This server allows you to:\n"
        "- List repositories for the authenticated user\n"
        "- Get detailed repository information\n"
        "- Search repositories on GitHub\n"
        "- Get repository contents and file information\n\n"
        "Authentication is handled via GITHUB_TOKEN environment variable."
    ),
    host="0.0.0.0",
    port=PORT,
)

# -----------------------------
# Tools
# -----------------------------


@mcp.tool()
def list_repositories(
    type: Literal["all", "owner", "public", "private", "member"] = "all",
    sort: Literal["created", "updated", "pushed", "full_name"] = "updated",
    direction: Literal["asc", "desc"] = "desc",
    per_page: int = 30,
    page: int = 1,
) -> str:
    """List GitHub repositories for the authenticated user."""
    try:
        user = gh.get_user()

        # Emulate filters similar to Octokit
        if type == "owner":
            repos_iter = user.get_repos(affiliation="owner")
        elif type == "member":
            repos_iter = user.get_repos(affiliation="collaborator,organization_member")
        elif type == "public":
            repos_iter = (r for r in user.get_repos() if not r.private)
        elif type == "private":
            repos_iter = (r for r in user.get_repos() if r.private)
        else:  # "all"
            repos_iter = user.get_repos()

        sort_key = {
            "created": lambda r: r.created_at,
            "updated": lambda r: r.updated_at,
            "pushed": lambda r: r.pushed_at,
            "full_name": lambda r: r.full_name.lower(),
        }[sort]
        repos = sorted(repos_iter, key=sort_key, reverse=(direction == "desc"))

        start = (page - 1) * per_page
        end = start + per_page
        window = repos[start:end]

        payload = [
            {
                "name": r.name,
                "full_name": r.full_name,
                "description": r.description,
                "private": r.private,
                "html_url": r.html_url,
                "language": r.language,
                "stargazers_count": r.stargazers_count,
                "forks_count": r.forks_count,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in window
        ]

        return f"Found {len(payload)} repositories:\n\n" + json.dumps(payload, indent=2)

    except GithubException as e:
        raise RuntimeError(f"Failed to list repositories: {_err_msg(e)}")


@mcp.tool()
def get_repository(owner: str, repo: str) -> str:
    """Get detailed information about a specific repository."""
    try:
        r = gh.get_repo(f"{owner}/{repo}")
        # get_license() can 404 on repos without a license; protect it
        try:
            lic = r.get_license().license.spdx_id  # type: ignore[attr-defined]
        except Exception:
            lic = None

        info = {
            "name": r.name,
            "full_name": r.full_name,
            "description": r.description,
            "private": r.private,
            "html_url": r.html_url,
            "clone_url": r.clone_url,
            "ssh_url": r.ssh_url,
            "language": r.language,
            "stargazers_count": r.stargazers_count,
            "watchers_count": r.subscribers_count,
            "forks_count": r.forks_count,
            "open_issues_count": r.open_issues_count,
            "size": r.size,
            "default_branch": r.default_branch,
            "topics": r.get_topics(),
            "license": lic,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "pushed_at": r.pushed_at.isoformat() if r.pushed_at else None,
        }
        return "Repository Details:\n\n" + json.dumps(info, indent=2)

    except GithubException as e:
        raise RuntimeError(f"Failed to get repository {owner}/{repo}: {_err_msg(e)}")


@mcp.tool()
def search_repositories(
    q: str,
    sort: Optional[Literal["stars", "forks", "help-wanted-issues", "updated"]] = None,
    order: Literal["asc", "desc"] = "desc",
    per_page: int = 30,
    page: int = 1,
) -> str:
    """Search for repositories on GitHub."""
    try:
        results = gh.search_repositories(query=q, sort=sort or "", order=order)

        start = (page - 1) * per_page
        end = start + per_page
        items = list(results)[start:end]

        repos = [
            {
                "name": r.name,
                "full_name": r.full_name,
                "description": r.description,
                "html_url": r.html_url,
                "language": r.language,
                "stargazers_count": r.stargazers_count,
                "forks_count": r.forks_count,
                "score": getattr(r, "score", None),
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in items
        ]

        body = {
            "approx_total": getattr(results, "totalCount", None),
            "items": repos,
        }
        return "Search Results:\n\n" + json.dumps(body, indent=2)

    except GithubException as e:
        raise RuntimeError(f"Failed to search repositories: {_err_msg(e)}")


@mcp.tool()
def get_repository_contents(
    owner: str,
    repo: str,
    path: str = "",
    ref: Optional[str] = "main",
) -> str:
    """Get the contents of a repository directory or a single file (decoded text)."""
    try:
        r = gh.get_repo(f"{owner}/{repo}")
        try:
            contents = r.get_contents(path or "", ref=ref)
        except AssertionError as ae:
            return (
                f"[DEBUG] AssertionError while getting contents for {owner}/{repo}{('/' + path) if path else ''}: {repr(ae)}\n"
                f"path: {path!r}, ref: {ref!r}"
            )

        debug_info = (
            f"[DEBUG] contents type: {type(contents)}, repr: {repr(contents)[:500]}"
        )

        if isinstance(contents, list):
            listing = [
                {
                    "name": c.name,
                    "path": c.path,
                    "type": c.type,
                    "size": c.size,
                    "download_url": c.download_url,
                    "html_url": c.html_url,
                }
                for c in contents
            ]
            return (
                debug_info
                + "\n"
                + f"Directory Contents ({path or 'root'}):\n\n"
                + json.dumps(listing, indent=2)
            )
        else:
            try:
                if contents.encoding == "base64" and contents.content:
                    raw_bytes = base64.b64decode(contents.content)
                    try:
                        text = raw_bytes.decode("utf-8", errors="replace")
                    except Exception:
                        text = None
                else:
                    try:
                        decoded = getattr(contents, "decoded_content", b"")
                        text = decoded.decode("utf-8", errors="replace")
                    except Exception:
                        text = None

                is_binary = False
                if text is not None:
                    sample = text[:100]
                    non_printable = sum(
                        1 for c in sample if ord(c) < 9 or (13 < ord(c) < 32)
                    )
                    if non_printable > 10:
                        is_binary = True

                if text is None:
                    preview = "[Unable to decode file contents]"
                elif is_binary:
                    preview = "[Binary file preview omitted]"
                else:
                    preview = (
                        text
                        if len(text) <= 4000
                        else text[:4000] + "\n...[truncated]..."
                    )

                file_info = {
                    "name": contents.name,
                    "path": contents.path,
                    "type": contents.type,
                    "size": contents.size,
                    "download_url": contents.download_url,
                    "html_url": contents.html_url,
                    "preview": preview,
                }
                return (
                    debug_info
                    + "\n"
                    + "File Details:\n\n"
                    + json.dumps(file_info, indent=2)
                )
            except Exception as e:
                return (
                    debug_info
                    + "\n"
                    + (
                        f"Unexpected error decoding file for {owner}/{repo}{('/' + path) if path else ''}: {type(e).__name__}: {repr(e)}"
                    )
                )
    except GithubException as e:
        raise RuntimeError(
            f"Failed to get contents for {owner}/{repo}{('/' + path) if path else ''}: {_err_msg(e)}"
        )
    except Exception as e:
        raise RuntimeError(
            f"Unexpected error while getting contents for {owner}/{repo}{('/' + path) if path else ''}: {type(e).__name__}: {repr(e)}"
        )


# -----------------------------
# Entrypoints (stdio vs HTTP)
# -----------------------------
if __name__ == "__main__":
    if os.getenv("NODE_ENV") == "production":
        logger.info("Running in production mode, starting HTTP server...")
        mcp.settings.stateless_http = True
        mcp.run(transport="streamable-http")
    else:
        logger.info("Running in development mode, starting stdio server...")
        mcp.run(transport="stdio")

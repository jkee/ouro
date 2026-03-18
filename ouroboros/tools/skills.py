"""Agent Skills (skills.sh) tools — discover, activate, install, and search skills.

Skills are stored in .agents/skills/ in the repo, versioned in git.
Each skill is a directory containing a SKILL.md with YAML frontmatter.
Uses progressive disclosure: Tier 1 (name+desc) in context, Tier 2 (full body) on activation.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ouroboros.tools.registry import ToolEntry, ToolContext

log = logging.getLogger(__name__)

SKILLS_REL = ".agents/skills"

# --- Frontmatter parsing ---


def _parse_skill_md(path: Path) -> Optional[Dict[str, Any]]:
    """Parse a SKILL.md file, returning {name, description, body, metadata} or None."""
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return None

    # Split on YAML frontmatter delimiters
    parts = re.split(r"^---\s*$", content, maxsplit=2, flags=re.MULTILINE)
    if len(parts) < 3:
        # No valid frontmatter — treat entire content as body
        return {"name": path.parent.name, "description": "", "body": content.strip(), "metadata": {}}

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}

    return {
        "name": meta.get("name", path.parent.name),
        "description": meta.get("description", ""),
        "body": parts[2].strip(),
        "metadata": meta,
    }


def _skills_dir(ctx: ToolContext) -> Path:
    return ctx.repo_path(SKILLS_REL)


def _list_resource_files(skill_dir: Path) -> List[str]:
    """List non-SKILL.md files in a skill directory."""
    files = []
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file() and p.name != "SKILL.md":
            files.append(str(p.relative_to(skill_dir)))
    return files


# --- Tool handlers ---


def _skill_list(ctx: ToolContext, **kwargs) -> str:
    sdir = _skills_dir(ctx)
    if not sdir.exists():
        return "No skills installed. Use skill_install or skill_search to add skills."

    skills = []
    for skill_md in sorted(sdir.glob("*/SKILL.md")):
        parsed = _parse_skill_md(skill_md)
        if parsed:
            skills.append(parsed)

    if not skills:
        return "No skills installed. Use skill_install or skill_search to add skills."

    lines = [f"**{len(skills)} installed skill(s):**\n"]
    for s in skills[:20]:
        desc = s["description"][:120] if s["description"] else "(no description)"
        lines.append(f"- **{s['name']}**: {desc}")

    if len(skills) > 20:
        lines.append(f"\n... and {len(skills) - 20} more.")

    return "\n".join(lines)


def _skill_activate(ctx: ToolContext, name: str = "", **kwargs) -> str:
    if not name:
        return "Error: skill name is required."

    # Sanitize: no path traversal
    if "/" in name or "\\" in name or ".." in name:
        return f"Error: invalid skill name: {name}"

    sdir = _skills_dir(ctx)
    skill_md = sdir / name / "SKILL.md"

    if not skill_md.exists():
        return f"Error: skill '{name}' not found. Use skill_list to see installed skills."

    parsed = _parse_skill_md(skill_md)
    if not parsed:
        return f"Error: could not parse SKILL.md for '{name}'."

    # Build activation response
    parts = [f"# Skill: {parsed['name']}\n"]
    if parsed["description"]:
        parts.append(f"**Description:** {parsed['description']}\n")
    parts.append(parsed["body"])

    # List resource files if any
    resources = _list_resource_files(sdir / name)
    if resources:
        parts.append(f"\n\n**Resource files available** (use repo_read to load):")
        for r in resources[:20]:
            parts.append(f"- `{SKILLS_REL}/{name}/{r}`")

    return "\n".join(parts)


def _skill_install(ctx: ToolContext, source: str = "", **kwargs) -> str:
    if not source:
        return "Error: source is required (e.g. 'vercel-labs/skills@find-skills' or a GitHub URL)."

    sdir = _skills_dir(ctx)
    sdir.mkdir(parents=True, exist_ok=True)

    # Build npx command
    # Support both "owner/repo@skill" and "https://github.com/owner/repo --skill name" forms
    cmd = ["npx", "-y", "skills", "add", source]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(ctx.repo_dir),
            timeout=120,
            capture_output=True,
            text=True,
        )
        output = (result.stdout + "\n" + result.stderr).strip()

        if result.returncode != 0:
            return f"skill_install failed (exit {result.returncode}):\n{output}"

        # Return summary of what was installed
        parts = [f"Installed from {source}."]
        if output:
            parts.append(f"\nOutput:\n{output[:2000]}")

        # Show updated catalog
        parts.append(f"\nUse skill_list to see all installed skills.")
        return "\n".join(parts)

    except subprocess.TimeoutExpired:
        return f"Error: skill_install timed out after 120s for source: {source}"
    except FileNotFoundError:
        return "Error: npx not found. Node.js is required for skills.sh CLI."
    except Exception as e:
        return f"Error installing skill: {e}"


def _skill_search(ctx: ToolContext, query: str = "", **kwargs) -> str:
    if not query:
        return "Error: search query is required."

    try:
        result = subprocess.run(
            ["npx", "-y", "skills", "find", query],
            cwd=str(ctx.repo_dir),
            timeout=60,
            capture_output=True,
            text=True,
        )
        output = (result.stdout + "\n" + result.stderr).strip()

        if result.returncode != 0:
            return f"skill_search failed (exit {result.returncode}):\n{output}"

        if not output:
            return f"No skills found for query: {query}"

        return f"Skills matching '{query}':\n\n{output[:3000]}\n\nUse skill_install(source) to install."

    except subprocess.TimeoutExpired:
        return f"Error: skill search timed out after 60s for query: {query}"
    except FileNotFoundError:
        return "Error: npx not found. Node.js is required for skills.sh CLI."
    except Exception as e:
        return f"Error searching skills: {e}"


# --- Registration ---


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            name="skill_list",
            schema={
                "name": "skill_list",
                "description": (
                    "List all installed Agent Skills with name and description. "
                    "Skills are in .agents/skills/ in the repo. Use this to discover "
                    "available skills before activating one."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            handler=_skill_list,
        ),
        ToolEntry(
            name="skill_activate",
            schema={
                "name": "skill_activate",
                "description": (
                    "Load a skill's full instructions (SKILL.md body). "
                    "Use when a task matches a skill's description from skill_list. "
                    "Returns the skill's instructions and lists any resource files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Skill name (directory name under .agents/skills/)",
                        }
                    },
                    "required": ["name"],
                },
            },
            handler=_skill_activate,
        ),
        ToolEntry(
            name="skill_install",
            schema={
                "name": "skill_install",
                "description": (
                    "Install an Agent Skill from skills.sh. "
                    "Source can be 'owner/repo@skill-name' or a GitHub URL. "
                    "Example: skill_install(source='vercel-labs/skills@find-skills'). "
                    "After installing, commit via repo_commit_push to persist in git."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Skill source (e.g. 'vercel-labs/skills@find-skills')",
                        }
                    },
                    "required": ["source"],
                },
            },
            handler=_skill_install,
            timeout_sec=120,
        ),
        ToolEntry(
            name="skill_search",
            schema={
                "name": "skill_search",
                "description": (
                    "Search the skills.sh leaderboard for Agent Skills matching a query. "
                    "Returns skill names, install counts, and sources. "
                    "Use skill_install to install a result."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g. 'react', 'testing', 'code review')",
                        }
                    },
                    "required": ["query"],
                },
            },
            handler=_skill_search,
            timeout_sec=60,
        ),
    ]

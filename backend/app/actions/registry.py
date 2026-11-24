from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.actions.sanitize import neutralize_github, neutralize_slack


class CreateGithubIssueArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=6000)
    labels: list[Annotated[str, Field(max_length=50)]] = Field(
        default_factory=list, max_length=5
    )


class PostSlackAlertArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class ToolSpec:
    args_model: type[BaseModel]
    integration_kind: str
    requires_approval: bool


TOOLS = {
    "create_github_issue": ToolSpec(CreateGithubIssueArgs, "github", True),
    "post_slack_alert": ToolSpec(PostSlackAlertArgs, "slack", True),
}


class UnknownTool(Exception):
    pass


def validate_arguments(tool: str, raw: dict) -> dict:
    spec = TOOLS.get(tool)
    if spec is None:
        raise UnknownTool(f"Unknown tool: {tool}")
    args = spec.args_model.model_validate(raw)
    result = args.model_dump()
    if tool == "create_github_issue":
        result["title"] = neutralize_github(result["title"])
        result["body"] = neutralize_github(result["body"])
    elif tool == "post_slack_alert":
        result["text"] = neutralize_slack(result["text"])
    return result

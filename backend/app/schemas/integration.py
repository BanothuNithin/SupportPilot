import re

from pydantic import BaseModel, Field, field_validator


GITHUB_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GithubIntegrationIn(BaseModel):
    repo: str
    token: str = Field(min_length=20, max_length=255)

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str) -> str:
        if not GITHUB_REPO_PATTERN.fullmatch(value):
            raise ValueError("repo must be owner/repository")
        owner, repository = value.split("/", 1)
        if owner in {".", ".."} or repository in {".", ".."}:
            raise ValueError("repo must be owner/repository")
        return value

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        if any(char.isspace() for char in value):
            raise ValueError("token must not contain whitespace")
        return value


class SlackIntegrationIn(BaseModel):
    webhook_url: str


class IntegrationOut(BaseModel):
    kind: str
    configured: bool
    summary: str

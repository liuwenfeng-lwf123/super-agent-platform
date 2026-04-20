from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    thread_id: str = ""
    role: str
    content: str
    agent_id: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


class Thread(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "New Chat"
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    metadata: dict = Field(default_factory=dict)


class ChatRequest(BaseModel):
    thread_id: Optional[str] = None
    message: str
    model: Optional[str] = None
    skills: Optional[list[str]] = None
    mode: Optional[str] = "standard"


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    key: str
    value: str
    category: str = "knowledge"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    access_count: int = 0


class SkillConfig(BaseModel):
    name: str
    display_name: str
    description: str
    enabled: bool = True
    built_in: bool = True
    system_prompt: str
    tools: list[str] = Field(default_factory=list)


class ModelConfig(BaseModel):
    name: str
    display_name: str
    provider: str = "openai"
    api_key_env: str = "OPENAI_API_KEY"
    base_url: str = ""
    model: str = ""
    max_tokens: int = 4096
    supports_streaming: bool = True


class SSEEvent(BaseModel):
    type: str
    data: dict = Field(default_factory=dict)

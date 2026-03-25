from pydantic import BaseModel, Field


class NodeBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    host: str = Field(..., min_length=1, max_length=255)
    web_port: int = Field(default=443, ge=1, le=65535)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    location: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    notes: str | None = None
    api_username: str | None = Field(default=None, max_length=255)
    api_password: str | None = Field(default=None, max_length=255)
    api_use_https: bool = False


class NodeCreate(NodeBase):
    pass


class NodeUpdate(NodeBase):
    pass


class ServiceCheckBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    service_type: str = Field(default="url", pattern="^(url|dns)$")
    target: str = Field(..., min_length=1, max_length=512)
    enabled: bool = True
    notes: str | None = None


class ServiceCheckCreate(ServiceCheckBase):
    pass

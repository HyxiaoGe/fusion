from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_current_admin_user
from app.db.models import User as UserModel
from app.schemas.response import success
from app.services.external import search_usage_client
from app.services.external.search_usage_client import SearchUsageClientError
from app.services.prompt_bundle_hold_service import (
    enter_prompt_bundle_hold,
    get_prompt_bundle_hold,
    release_prompt_bundle_hold,
)
from app.services.runtime_config_governance import (
    activate_runtime_config_entry,
    build_runtime_config_snapshot,
    create_runtime_config_entry,
    set_runtime_config_entry_active,
    validate_runtime_config_candidate,
)

router = APIRouter()


class RuntimeConfigValidateRequest(BaseModel):
    namespace: str
    key: str
    payload: dict[str, Any]


class RuntimeConfigCreateRequest(BaseModel):
    namespace: str
    key: str
    version: str
    payload: dict[str, Any]
    description: str | None = None


class RuntimeConfigStatusRequest(BaseModel):
    is_active: bool


@router.get("/search-usage")
async def get_search_usage(_admin: UserModel = Depends(get_current_admin_user)):
    try:
        firecrawl_usage = await search_usage_client.get_firecrawl_usage()
    except SearchUsageClientError as exc:
        raise HTTPException(status_code=502, detail="联网用量查询失败") from exc

    try:
        firecrawl_historical = await search_usage_client.get_firecrawl_historical_usage()
    except SearchUsageClientError:
        firecrawl_historical = {
            "provider": "firecrawl",
            "available": False,
            "by_api_key": False,
            "periods": [],
        }

    return success(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "providers": [
                {"provider": "firecrawl", "official_usage": True},
                {"provider": "brave", "official_usage": False},
            ],
            "firecrawl": firecrawl_usage,
            "historical": firecrawl_historical,
        }
    )


@router.get("/runtime-config")
async def get_runtime_config_snapshot(_admin: UserModel = Depends(get_current_admin_user)):
    return success(build_runtime_config_snapshot())


@router.post("/runtime-config/validate")
async def validate_runtime_config(
    request: RuntimeConfigValidateRequest,
    _admin: UserModel = Depends(get_current_admin_user),
):
    return success(validate_runtime_config_candidate(request.namespace, request.key, request.payload))


@router.post("/runtime-config")
async def create_runtime_config(
    request: RuntimeConfigCreateRequest,
    _admin: UserModel = Depends(get_current_admin_user),
):
    return success(
        create_runtime_config_entry(
            namespace=request.namespace,
            key=request.key,
            version=request.version,
            payload=request.payload,
            description=request.description,
        )
    )


@router.post("/runtime-config/{entry_id}/activate")
async def activate_runtime_config(
    entry_id: str,
    _admin: UserModel = Depends(get_current_admin_user),
):
    return success(activate_runtime_config_entry(entry_id))


@router.patch("/runtime-config/{entry_id}/status")
async def update_runtime_config_status(
    entry_id: str,
    request: RuntimeConfigStatusRequest,
    _admin: UserModel = Depends(get_current_admin_user),
):
    return success(set_runtime_config_entry_active(entry_id, request.is_active))


class PromptBundleHoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


@router.get("/prompt-bundle/hold")
def read_prompt_bundle_hold(_admin: UserModel = Depends(get_current_admin_user)):
    return success(get_prompt_bundle_hold())


@router.post("/prompt-bundle/hold")
def hold_prompt_bundle(request: PromptBundleHoldRequest, admin: UserModel = Depends(get_current_admin_user)):
    return success(enter_prompt_bundle_hold(request.target_revision, actor=admin.id, reason=request.reason))


@router.post("/prompt-bundle/hold/release")
def release_held_prompt_bundle(request: PromptBundleHoldRequest, admin: UserModel = Depends(get_current_admin_user)):
    return success(release_prompt_bundle_hold(request.target_revision, actor=admin.id, reason=request.reason))

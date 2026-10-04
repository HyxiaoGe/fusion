from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_admin_user, get_model_management_service
from app.db.models import User
from app.schemas.model_management import ModelVisibilityRequest
from app.schemas.response import success
from app.services.model_management_service import ModelManagementService

router = APIRouter()


@router.get("")
def get_model_management_snapshot(
    request: Request,
    _admin: User = Depends(get_current_admin_user),
    service: ModelManagementService = Depends(get_model_management_service),
):
    return success(data=service.get_snapshot(), request_id=request.state.request_id)


@router.patch("/models/visibility")
def update_model_visibility(
    payload: ModelVisibilityRequest,
    request: Request,
    admin: User = Depends(get_current_admin_user),
    service: ModelManagementService = Depends(get_model_management_service),
):
    row = service.set_visibility(
        model_id=payload.model_id,
        selectable=payload.selectable,
        expected_revision=payload.expected_revision,
        reason=payload.reason,
        admin=admin,
        request_id=request.state.request_id,
    )
    return success(
        data={
            "model_id": row.model_id,
            "selectable": bool(row.selectable),
            "routable": True,
            "revision": row.revision,
            "reason": row.reason,
            "updated_at": row.updated_at,
        },
        request_id=request.state.request_id,
    )

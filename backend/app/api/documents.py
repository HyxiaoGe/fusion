"""交付物文档读取接口；写入只经由 Agent 工具，接口不提供修改。"""

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.models import User
from app.schemas.document import (
    DocumentDetailResponse,
    DocumentVersionResponse,
    DocumentVersionSummaryResponse,
)
from app.schemas.response import ApiException, success
from app.services.documents.service import DocumentError, DocumentService

router = APIRouter()

_NOT_FOUND_MESSAGE = "文档不存在"


@router.get("/{document_id}")
def get_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        detail = DocumentService(db).get_detail(document_id, user_id=current_user.id)
    except DocumentError:
        raise ApiException.not_found(_NOT_FOUND_MESSAGE) from None
    payload = DocumentDetailResponse(
        id=detail.document_id,
        conversation_id=detail.conversation_id,
        title=detail.title,
        format=detail.format,
        current_version=detail.current_version,
        versions=[
            DocumentVersionSummaryResponse(
                version=item.version,
                title=item.title,
                change_summary=item.change_summary,
                char_count=item.char_count,
                created_at=item.created_at,
            )
            for item in detail.versions
        ],
        created_at=detail.created_at,
        updated_at=detail.updated_at,
    )
    return success(data=payload.model_dump(mode="json"), request_id=request.state.request_id)


@router.get("/{document_id}/content")
def get_document_content(
    document_id: str,
    request: Request,
    version: Optional[int] = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        snapshot = DocumentService(db).get_version(document_id, user_id=current_user.id, version=version)
    except DocumentError:
        raise ApiException.not_found(_NOT_FOUND_MESSAGE) from None
    payload = DocumentVersionResponse(
        document_id=snapshot.document_id,
        version=snapshot.version,
        title=snapshot.title,
        format=snapshot.format,
        content=snapshot.content,
        change_summary=snapshot.change_summary,
        sources=list(snapshot.sources),
        created_at=snapshot.created_at,
    )
    return success(data=payload.model_dump(mode="json"), request_id=request.state.request_id)

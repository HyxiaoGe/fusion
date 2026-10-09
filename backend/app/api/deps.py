"""
API 层依赖注入工厂

所有 Service / Repository 实例通过这里的工厂函数创建，
路由层通过 Depends() 注入，不再手动 new。
"""

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_current_user  # noqa: F401 — re-export
from app.db.admin_audit_repository import AdminAuditRepository
from app.db.database import get_db  # noqa: F401 — re-export
from app.db.mcp_server_repository import McpServerRepository
from app.db.model_catalog_control_repository import ModelCatalogControlRepository
from app.db.models import User as UserModel
from app.db.repositories import UserRepository
from app.db.trajectory_repository import TrajectoryRepository
from app.services.admin_audit_service import AdminAuditService
from app.services.changelog_service import ChangelogService
from app.services.chat_service import ChatService
from app.services.file_service import FileService
from app.services.knowledge.service import KnowledgeService
from app.services.mcp.runtime import get_mcp_client_manager
from app.services.mcp.server_service import McpServerService
from app.services.model_management_service import ModelManagementConfig, ModelManagementService
from app.services.notification_service import NotificationService
from app.services.trajectory_query_service import TrajectoryQueryService


def get_chat_service(db: Session = Depends(get_db)) -> ChatService:
    return ChatService(db)


def get_file_service(db: Session = Depends(get_db)) -> FileService:
    return FileService(db)


def get_knowledge_service(db: Session = Depends(get_db)) -> KnowledgeService:
    return KnowledgeService(db)


def get_notification_service(db: Session = Depends(get_db)) -> NotificationService:
    return NotificationService(db)


def get_changelog_service(db: Session = Depends(get_db)) -> ChangelogService:
    return ChangelogService(db)


def get_trajectory_query_service(db: Session = Depends(get_db)) -> TrajectoryQueryService:
    return TrajectoryQueryService(
        TrajectoryRepository(db),
        max_events_per_run=settings.MAX_TRAJECTORY_EVENTS_PER_RUN,
        max_runs_per_conversation=settings.MAX_TRAJECTORY_RUNS_PER_CONVERSATION,
        detail_settle_grace_seconds=settings.TRAJECTORY_DETAIL_SETTLE_GRACE_SECONDS,
    )


def get_user_repo(db: Session = Depends(get_db)) -> UserRepository:
    return UserRepository(db)


def get_admin_audit_service(db: Session = Depends(get_db)) -> AdminAuditService:
    return AdminAuditService(AdminAuditRepository(db))


def get_mcp_server_service(db: Session = Depends(get_db)) -> McpServerService:
    return McpServerService(McpServerRepository(db), get_mcp_client_manager())


def get_model_catalog_control_repository(
    db: Session = Depends(get_db),
) -> ModelCatalogControlRepository:
    return ModelCatalogControlRepository(db)


def get_model_management_service(db: Session = Depends(get_db)) -> ModelManagementService:
    return ModelManagementService(
        control_repository=ModelCatalogControlRepository(db),
        audit_service=AdminAuditService(AdminAuditRepository(db)),
        config=ModelManagementConfig(management_enabled=settings.LITELLM_MODEL_MANAGEMENT_ENABLED),
    )


def get_current_admin_user(
    current_user: UserModel = Depends(get_current_user),
) -> UserModel:
    """要求 is_superuser=True，否则 403。用于 admin 专属端点。"""
    if not getattr(current_user, "is_superuser", False):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current_user


def get_conversation_auditor(
    current_user: UserModel = Depends(get_current_user),
) -> UserModel:
    """独立的全局会话只读审计权限边界；v1 映射现有超级管理员。"""
    if not getattr(current_user, "is_superuser", False):
        raise HTTPException(status_code=403, detail="需要会话审计员权限")
    return current_user

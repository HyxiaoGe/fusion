from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from sqlalchemy.exc import IntegrityError

from app.ai import litellm_catalog, litellm_health
from app.db.model_catalog_control_repository import ModelCatalogControlRepository
from app.db.models import ModelCatalogControl, User
from app.schemas.response import ApiException
from app.services.admin_audit_service import AdminAuditService
from app.utils.time import utc_now


@dataclass(frozen=True)
class ModelManagementConfig:
    management_enabled: bool


class ModelManagementService:
    """后台模型列表与可见性开关。模型上下线走 scripts/model_onboard.py。"""

    def __init__(
        self,
        *,
        control_repository: ModelCatalogControlRepository,
        audit_service: AdminAuditService,
        config: ModelManagementConfig,
        catalog: Any = litellm_catalog,
        clock: Callable[[], datetime] = utc_now,
    ):
        self.control_repository = control_repository
        self.audit_service = audit_service
        self.config = config
        self.catalog = catalog
        self.clock = clock

    @property
    def db(self):
        return self.control_repository.db

    def get_snapshot(self) -> dict[str, Any]:
        return {
            "generated_at": self._as_utc(self.clock()).isoformat(),
            "models": self.list_registered_models(),
        }

    def list_registered_models(self) -> list[dict[str, Any]]:
        catalog = self.catalog.list_aliases()
        aliases = sorted(alias for alias, entry in catalog.items() if entry.get("db_model"))
        controls = self.control_repository.get_by_model_ids(aliases)
        return [self._registered_model(alias, catalog[alias], controls.get(alias)) for alias in aliases]

    def set_visibility(
        self,
        *,
        model_id: str,
        selectable: bool,
        expected_revision: int | None,
        reason: str,
        admin: User,
        request_id: str,
    ) -> ModelCatalogControl:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ApiException.bad_request("变更原因不能为空")
        if len(normalized_reason) > 300:
            raise ApiException.bad_request("变更原因不能超过 300 个字符")
        if not self.config.management_enabled:
            raise ApiException.service_unavailable("模型管理写操作未启用")
        entry = self.catalog.get_model_entry(model_id)
        if not entry or not entry.get("db_model"):
            raise ApiException.not_found("模型不存在或尚未注册")
        try:
            row = self._write_visibility(
                model_id=model_id,
                selectable=selectable,
                expected_revision=expected_revision,
                reason=normalized_reason,
                updated_by=str(admin.id),
            )
            self.audit_service._record(
                admin=admin,
                action="model_visibility_changed",
                resource_type="model_catalog_control",
                resource_id=model_id,
                request_id=request_id,
                reason=normalized_reason,
                metadata={
                    "selectable": selectable,
                    "routable": True,
                    "revision": row.revision,
                    "expected_revision": expected_revision,
                },
                commit=False,
            )
            self.db.commit()
            self.db.refresh(row)
            return row
        except ApiException:
            self.db.rollback()
            raise
        except IntegrityError as exc:
            self.db.rollback()
            raise ApiException.conflict("模型可见性版本已变化") from exc
        except Exception:
            self.db.rollback()
            raise

    def _write_visibility(
        self,
        *,
        model_id: str,
        selectable: bool,
        expected_revision: int | None,
        reason: str,
        updated_by: str,
    ) -> ModelCatalogControl:
        existing = self.control_repository.get(model_id)
        values = {
            "selectable": selectable,
            "routable": True,
            "reason": reason,
            "updated_by": updated_by,
            "updated_at": self.clock(),
        }
        if existing is None:
            if expected_revision is not None:
                raise ApiException.conflict("模型可见性版本已变化")
            return self.control_repository.add({"model_id": model_id, "revision": 1, **values})
        if expected_revision is None:
            raise ApiException.conflict("模型可见性版本已变化")
        updated = self.control_repository.update_if_revision(
            model_id=model_id,
            expected_revision=expected_revision,
            values=values,
        )
        if updated is None:
            raise ApiException.conflict("模型可见性版本已变化")
        return updated

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _registered_model(alias: str, entry: Mapping[str, Any], control: ModelCatalogControl | None) -> dict[str, Any]:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), Mapping) else {}
        underlying = str(entry.get("underlying") or "")
        provider = str(metadata.get("provider_key") or "").strip().lower()
        if not provider:
            provider = underlying.split("/", 1)[0].lower() if "/" in underlying else "litellm"
        selectable = bool(control.selectable) if control is not None else True
        return {
            "model_id": alias,
            "name": str(metadata.get("display_name") or alias),
            "provider": provider,
            "provider_display": str(metadata.get("provider_display") or provider),
            "health": litellm_health.get_health(alias),
            "selectable": selectable,
            "routable": True,
            "state": "selectable" if selectable else "hidden",
            "revision": control.revision if control is not None else None,
            "reason": control.reason if control is not None else None,
            "updated_at": control.updated_at if control is not None else None,
        }

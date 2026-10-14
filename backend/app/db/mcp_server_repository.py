from collections.abc import Callable
from typing import Any

from sqlalchemy import update as sqlalchemy_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import McpServer

# 写入落库前、提交前调用，参数为服务 ID；用于把管理员审计写进同一事务。
BeforeCommit = Callable[[str], None]


class McpServerRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self) -> list[McpServer]:
        return self.db.query(McpServer).order_by(McpServer.created_at.desc(), McpServer.id.desc()).all()

    def list_enabled(self) -> list[McpServer]:
        """按稳定顺序返回启用的 MCP 服务，供运行级工具目录构建。"""

        return (
            self.db.query(McpServer)
            .filter(McpServer.is_enabled.is_(True))
            .order_by(McpServer.created_at.asc(), McpServer.id.asc())
            .all()
        )

    def get(self, server_id: str) -> McpServer | None:
        return (
            self.db.query(McpServer).execution_options(populate_existing=True).filter(McpServer.id == server_id).first()
        )

    def get_by_name(self, name: str) -> McpServer | None:
        return self.db.query(McpServer).filter(McpServer.name == name).first()

    def create(self, values: dict[str, Any], *, before_commit: BeforeCommit | None = None) -> McpServer:
        row = McpServer(**values)
        self.db.add(row)
        self._commit(before_commit and (lambda: before_commit(row.id)))
        self.db.refresh(row)
        return row

    def update(
        self,
        row: McpServer,
        values: dict[str, Any],
        *,
        before_commit: BeforeCommit | None = None,
    ) -> McpServer:
        statement = (
            sqlalchemy_update(McpServer)
            .where(McpServer.id == row.id)
            .values(**values, config_version=McpServer.config_version + 1)
        )
        self.db.execute(statement)
        self._commit(before_commit and (lambda: before_commit(row.id)))
        self.db.expire_all()
        return self.get(row.id)

    def update_if_version(
        self,
        server_id: str,
        expected_version: int,
        values: dict[str, Any],
        *,
        before_commit: BeforeCommit | None = None,
    ) -> McpServer | None:
        """仅在配置版本未变化时保存远端检测结果，避免覆盖并发管理操作。"""

        statement = (
            sqlalchemy_update(McpServer)
            .where(McpServer.id == server_id, McpServer.config_version == expected_version)
            .values(**values, config_version=McpServer.config_version + 1)
        )
        result = self.db.execute(statement)
        if result.rowcount != 1:
            self.db.rollback()
            self.db.expire_all()
            return None
        self._commit(before_commit and (lambda: before_commit(server_id)))
        self.db.expire_all()
        return self.get(server_id)

    def commit_only(self, before_commit: BeforeCommit, server_id: str) -> None:
        """没有配置写入时单独提交审计等附带记录。"""

        self._commit(lambda: before_commit(server_id))

    def _commit(self, before_commit: Callable[[], None] | None = None) -> None:
        try:
            if before_commit is not None:
                self.db.flush()
                before_commit()
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise

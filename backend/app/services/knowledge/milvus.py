from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from app.ai.embeddings.base import EmbeddingProfile
from app.core.config import KNOWLEDGE_MILVUS_FILTER_TERM_BATCH_SIZE, settings
from app.services.knowledge.chunker import KnowledgeChunk, knowledge_index_text

logger = logging.getLogger(__name__)


# Milvus merr.ErrParameterInvalid：请求参数或数据不合法。
_MILVUS_PARAMETER_INVALID = 1100


class KnowledgeVectorError(RuntimeError):
    def __init__(self, code: str, summary: str, *, retryable: bool):
        self.code = code
        self.summary = summary
        self.retryable = retryable
        super().__init__(summary)


@dataclass(frozen=True)
class KnowledgeVectorRecord:
    chunk: KnowledgeChunk
    vector: list[float]
    user_id: str
    knowledge_base_id: str
    document_id: str
    index_version: str
    filename: str


@dataclass(frozen=True)
class KnowledgeVectorHit:
    chunk_id: str
    document_id: str
    knowledge_base_id: str
    index_version: str
    text: str
    similarity: float
    filename: str
    char_start: int
    char_end: int
    page: int | None
    section: str | None


class MilvusKnowledgeStore:
    """受控 collection 的 Milvus v2 适配器。

    text 字段走 Milvus 中文分词并由 BM25 Function 生成稀疏向量；检索时稠密与 BM25 两路召回后
    由 Milvus RRF 合并。knowledge_base_id 作为分区键。
    """

    SCHEMA_VERSION = "v2"
    BM25_ANALYZER_PARAMS = {"type": "chinese"}
    BM25_FUNCTION_NAME = "text_bm25"
    RRF_K = 60
    _INT64_TYPE = 5
    _VARCHAR_TYPE = 21
    _FLOAT_VECTOR_TYPE = 101
    _SPARSE_FLOAT_VECTOR_TYPE = 104
    _BM25_FUNCTION_TYPE = 1
    _INDEX_FIELDS = ("vector", "sparse")
    _VARCHAR_FIELDS = {
        "chunk_id": 64,
        "user_id": 64,
        "knowledge_base_id": 64,
        "document_id": 64,
        "index_version": 64,
        "text": 65535,
        "filename": 512,
        "section": 120,
    }
    _INT64_FIELDS = {"chunk_ordinal", "char_start", "char_end", "page"}

    def __init__(self, client_factory: Callable[[], Any] | None = None):
        self._client_factory = client_factory

    @classmethod
    def collection_name(cls, dimension: int) -> str:
        prefix = re.sub(r"[^a-zA-Z0-9_]", "_", settings.MILVUS_COLLECTION_PREFIX).strip("_")
        if not prefix:
            raise KnowledgeVectorError("KNOWLEDGE_VECTOR_CONFIG_INVALID", "Milvus collection 前缀无效", retryable=False)
        if dimension not in settings.RESOLVED_KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS:
            raise KnowledgeVectorError(
                "KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH",
                "Embedding 维度未进入 Milvus collection 白名单",
                retryable=False,
            )
        return f"{prefix}_{cls.SCHEMA_VERSION}_d{dimension}"

    async def health(self) -> None:
        await self._call(lambda client: client.list_collections(timeout=settings.MILVUS_TIMEOUT_SECONDS))

    async def ensure_collection(self, profile: EmbeddingProfile) -> str:
        collection = self._profile_collection(profile)

        def ensure(client: Any) -> None:
            if client.has_collection(collection_name=collection, timeout=settings.MILVUS_TIMEOUT_SECONDS):
                description = client.describe_collection(
                    collection_name=collection,
                    timeout=settings.MILVUS_TIMEOUT_SECONDS,
                )
                self._validate_collection(description, profile.dimension)
                index_descriptions = self._describe_indexes(client, collection)
                indexed_fields = {
                    description.get("field_name") for description in index_descriptions if isinstance(description, dict)
                }
                missing = [field for field in self._INDEX_FIELDS if field not in indexed_fields]
                if missing:
                    self._create_indexes(client, collection, profile.distance_metric, fields=missing)
                    index_descriptions = self._describe_indexes(client, collection)
                self._validate_indexes(index_descriptions, profile.distance_metric)
            else:
                self._create_collection(client, collection, profile.dimension, profile.distance_metric)
            self._load_collection(client, collection)

        await self._call(ensure, profile=profile)
        return collection

    async def upsert(self, profile: EmbeddingProfile, records: Sequence[KnowledgeVectorRecord]) -> None:
        if not records:
            return
        collection = await self.ensure_collection(profile)
        await self.upsert_prepared(profile, collection, records)

    async def upsert_prepared(
        self,
        profile: EmbeddingProfile,
        collection: str,
        records: Sequence[KnowledgeVectorRecord],
    ) -> None:
        """复用已完成 schema 校验的 collection，并保持公共入口相同的分批防线。"""
        if collection != self._profile_collection(profile):
            raise KnowledgeVectorError(
                "KNOWLEDGE_VECTOR_CONFIG_INVALID",
                "已准备的 Milvus collection 与 Embedding profile 不一致",
                retryable=False,
            )
        batch_size = settings.KNOWLEDGE_EMBEDDING_BATCH_SIZE
        for offset in range(0, len(records), batch_size):
            await self._upsert_batch(profile, collection, records[offset : offset + batch_size])

    async def _upsert_batch(
        self,
        profile: EmbeddingProfile,
        collection: str,
        records: Sequence[KnowledgeVectorRecord],
    ) -> None:
        data = [self._record_payload(record) for record in records]
        result = await self._call(
            lambda client: client.upsert(
                collection_name=collection,
                data=data,
                timeout=settings.MILVUS_TIMEOUT_SECONDS,
            ),
            profile=profile,
        )
        if isinstance(result, dict):
            upserted = result.get("upsert_count")
            if upserted is not None and int(upserted) != len(records):
                raise KnowledgeVectorError(
                    "KNOWLEDGE_VECTOR_WRITE_INCOMPLETE",
                    "Milvus 写入数量校验失败",
                    retryable=True,
                )
        expected_ids = {record.chunk.chunk_id for record in records}
        actual_ids = await self._readback_chunk_ids(profile, collection, sorted(expected_ids))
        if not expected_ids.issubset(actual_ids):
            raise KnowledgeVectorError(
                "KNOWLEDGE_VECTOR_WRITE_INCOMPLETE",
                "Milvus 写入完整性校验失败",
                retryable=True,
            )

    async def search(
        self,
        *,
        profile: EmbeddingProfile,
        query_vector: list[float],
        query_text: str,
        user_id: str,
        knowledge_base_ids: Sequence[str],
        index_versions: Sequence[str],
        limit: int,
    ) -> list[KnowledgeVectorHit]:
        if not knowledge_base_ids or not index_versions:
            return []
        collection = self._profile_collection(profile)
        unique_versions = sorted(set(index_versions))
        output_fields = [
            "document_id",
            "knowledge_base_id",
            "index_version",
            "text",
            "filename",
            "char_start",
            "char_end",
            "page",
            "section",
        ]

        def search_batches(client: Any) -> list[Any]:
            self._load_collection(client, collection)
            rows: list[Any] = []
            for offset in range(0, len(unique_versions), KNOWLEDGE_MILVUS_FILTER_TERM_BATCH_SIZE):
                version_batch = unique_versions[offset : offset + KNOWLEDGE_MILVUS_FILTER_TERM_BATCH_SIZE]
                search_filter = self.build_search_filter(user_id, knowledge_base_ids, version_batch)
                result = self._hybrid_search(
                    client,
                    collection=collection,
                    query_vector=query_vector,
                    query_text=query_text,
                    search_filter=search_filter,
                    metric=profile.distance_metric,
                    limit=limit,
                    output_fields=output_fields,
                )
                rows.extend(result[0] if result else [])
            return rows

        rows = await self._call(search_batches, profile=profile)
        hits_by_key: dict[tuple[str, str, str, str], KnowledgeVectorHit] = {}
        for row in rows:
            hit = self._hit_from_result(row)
            key = (hit.knowledge_base_id, hit.document_id, hit.index_version, hit.chunk_id)
            previous = hits_by_key.get(key)
            if previous is None or hit.similarity > previous.similarity:
                hits_by_key[key] = hit
        return sorted(
            hits_by_key.values(),
            key=lambda hit: (-hit.similarity, hit.chunk_id, hit.document_id, hit.index_version),
        )[:limit]

    @classmethod
    def _hybrid_search(
        cls,
        client: Any,
        *,
        collection: str,
        query_vector: list[float],
        query_text: str,
        search_filter: str,
        metric: str,
        limit: int,
        output_fields: list[str],
    ) -> Any:
        from pymilvus import AnnSearchRequest, RRFRanker

        # 两路各自召回 limit 条后按排名融合：稠密向量管语义，BM25 管型号、版本号等精确词。
        # 融合后的 distance 是 RRF 分数，只用于排序，不再是余弦相似度。
        requests = [
            AnnSearchRequest(
                data=[query_vector],
                anns_field="vector",
                param={"metric_type": metric},
                limit=limit,
                expr=search_filter,
            ),
            AnnSearchRequest(
                data=[query_text],
                anns_field="sparse",
                param={"metric_type": "BM25"},
                limit=limit,
                expr=search_filter,
            ),
        ]
        return client.hybrid_search(
            collection_name=collection,
            reqs=requests,
            ranker=RRFRanker(cls.RRF_K),
            limit=limit,
            output_fields=output_fields,
            consistency_level="Strong",
            timeout=settings.MILVUS_TIMEOUT_SECONDS,
        )

    async def delete_document(self, profile: EmbeddingProfile, document_id: str) -> None:
        await self._delete(profile, f"document_id == {self._quote(document_id)}")

    async def delete_knowledge_base(self, profile: EmbeddingProfile, knowledge_base_id: str) -> None:
        await self._delete(profile, f"knowledge_base_id == {self._quote(knowledge_base_id)}")

    async def delete_index_version(self, profile: EmbeddingProfile, index_version: str) -> None:
        await self._delete(profile, f"index_version == {self._quote(index_version)}")

    async def _delete(self, profile: EmbeddingProfile, filter_expression: str) -> None:
        collection = self._profile_collection(profile)

        def delete(client: Any) -> None:
            if not client.has_collection(collection_name=collection, timeout=settings.MILVUS_TIMEOUT_SECONDS):
                return
            self._load_collection(client, collection)
            client.delete(
                collection_name=collection,
                filter=filter_expression,
                timeout=settings.MILVUS_TIMEOUT_SECONDS,
            )

        await self._call(delete, profile=profile)

    @staticmethod
    def _load_collection(client: Any, collection: str) -> None:
        """显式加载 collection，覆盖 Standalone 重启或释放后的冷状态。"""
        client.load_collection(
            collection_name=collection,
            replica_number=1,
            timeout=settings.MILVUS_TIMEOUT_SECONDS,
        )

    async def _readback_chunk_ids(
        self,
        profile: EmbeddingProfile,
        collection: str,
        chunk_ids: Sequence[str],
    ) -> set[str]:
        actual_ids: set[str] = set()
        batch_size = settings.KNOWLEDGE_EMBEDDING_BATCH_SIZE
        for offset in range(0, len(chunk_ids), batch_size):
            batch = list(chunk_ids[offset : offset + batch_size])
            rows = await self._call(
                lambda client, ids=batch: client.get(
                    collection_name=collection,
                    ids=ids,
                    output_fields=["chunk_id"],
                    consistency_level="Strong",
                    timeout=settings.MILVUS_TIMEOUT_SECONDS,
                ),
                profile=profile,
            )
            actual_ids.update(str(row.get("chunk_id", row.get("id", ""))) for row in rows)
        return actual_ids

    async def _call(self, operation: Callable[[Any], Any], *, profile: EmbeddingProfile | None = None) -> Any:
        def run() -> Any:
            try:
                client = self._client_factory() if self._client_factory is not None else self._build_client(profile)
                return operation(client)
            except KnowledgeVectorError:
                raise
            except Exception as exc:
                if getattr(exc, "code", None) == _MILVUS_PARAMETER_INVALID:
                    # Milvus 拒收请求（如字段超长）是数据或代码问题，重试不会成功，不能报成服务不可用。
                    logger.warning("Milvus 拒绝请求: %s", str(exc)[:300])
                    raise KnowledgeVectorError(
                        "KNOWLEDGE_VECTOR_REQUEST_REJECTED",
                        "Milvus 拒绝了索引请求",
                        retryable=False,
                    ) from exc
                # 对外统一为可重试的不可用错误，但真实原因必须留在日志里，否则地址错误会伪装成临时故障。
                logger.warning("Milvus 调用失败: %s: %s", type(exc).__name__, str(exc)[:300])
                raise KnowledgeVectorError(
                    "KNOWLEDGE_VECTOR_UNAVAILABLE",
                    "Milvus 暂时不可用",
                    retryable=True,
                ) from exc
            finally:
                if "client" in locals() and hasattr(client, "close"):
                    try:
                        client.close()
                    except Exception:
                        logger.warning("Milvus 客户端关闭失败", exc_info=True)

        return await asyncio.to_thread(run)

    @classmethod
    def _profile_collection(cls, profile: EmbeddingProfile) -> str:
        collection = profile.collection_name or cls.collection_name(profile.dimension)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,254}", collection):
            raise KnowledgeVectorError(
                "KNOWLEDGE_VECTOR_CONFIG_INVALID",
                "持久化的 Milvus collection 名称无效",
                retryable=False,
            )
        return collection

    @staticmethod
    def _record_payload(record: KnowledgeVectorRecord) -> dict[str, Any]:
        # sparse 字段由 BM25 Function 从 text 生成，写入时不能携带。
        return {
            "chunk_id": record.chunk.chunk_id,
            "user_id": record.user_id,
            "knowledge_base_id": record.knowledge_base_id,
            "document_id": record.document_id,
            "index_version": record.index_version,
            "chunk_ordinal": record.chunk.ordinal,
            "text": knowledge_index_text(record.chunk),
            "filename": record.filename,
            "char_start": record.chunk.char_start,
            "char_end": record.chunk.char_end,
            "page": record.chunk.page or 0,
            "section": record.chunk.section or "",
            "vector": record.vector,
        }

    @classmethod
    def build_search_filter(
        cls,
        user_id: str,
        knowledge_base_ids: Sequence[str],
        index_versions: Sequence[str],
    ) -> str:
        bases = ", ".join(cls._quote(value) for value in sorted(set(knowledge_base_ids)))
        versions = ", ".join(cls._quote(value) for value in sorted(set(index_versions)))
        return f"user_id == {cls._quote(user_id)} and knowledge_base_id in [{bases}] and index_version in [{versions}]"

    @staticmethod
    def _quote(value: str) -> str:
        if not value or len(value) > 200 or any(ord(character) < 32 for character in value):
            raise KnowledgeVectorError("KNOWLEDGE_VECTOR_FILTER_INVALID", "Milvus 过滤标识无效", retryable=False)
        return json.dumps(value, ensure_ascii=True)

    @staticmethod
    def _hit_from_result(row: Any) -> KnowledgeVectorHit:
        try:
            entity = row.get("entity", {}) if isinstance(row, dict) else getattr(row, "entity", {})
            read = entity.get if isinstance(entity, dict) else lambda name, default=None: getattr(entity, name, default)
            row_get = row.get if isinstance(row, dict) else lambda name, default=None: getattr(row, name, default)
            chunk_id = row_get("id", row_get("chunk_id", ""))
            document_id = read("document_id", "")
            knowledge_base_id = read("knowledge_base_id", "")
            index_version = read("index_version", "")
            similarity_value = row_get("distance", 0.0)
            char_start_value = read("char_start", 0)
            char_end_value = read("char_end", 0)
            page_value = read("page", 0)
            if (
                any(
                    not isinstance(value, str) or not value
                    for value in (chunk_id, document_id, knowledge_base_id, index_version)
                )
                or isinstance(similarity_value, bool)
                or isinstance(char_start_value, bool)
                or isinstance(char_end_value, bool)
                or isinstance(page_value, bool)
            ):
                raise ValueError("invalid Milvus hit field types")
            similarity = float(similarity_value)
            char_start = int(char_start_value)
            char_end = int(char_end_value)
            page_number = int(page_value)
            hit = KnowledgeVectorHit(
                chunk_id=chunk_id,
                document_id=document_id,
                knowledge_base_id=knowledge_base_id,
                index_version=index_version,
                text=str(read("text", "")),
                similarity=similarity,
                filename=str(read("filename", "")),
                char_start=char_start,
                char_end=char_end,
                page=page_number or None,
                section=str(read("section", "")) or None,
            )
            if (
                not math.isfinite(hit.similarity)
                or hit.char_start < 0
                or hit.char_end < hit.char_start
                or page_number < 0
            ):
                raise ValueError("invalid Milvus hit fields")
            return hit
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise KnowledgeVectorError(
                "KNOWLEDGE_VECTOR_RESPONSE_INVALID",
                "Milvus 检索响应不符合知识库契约",
                retryable=True,
            ) from exc

    @classmethod
    def _validate_collection(cls, description: dict[str, Any], expected_dimension: int) -> None:
        try:
            raw_fields = description.get("fields", [])
            fields = {field.get("name"): field for field in raw_fields}
            expected_names = set(cls._VARCHAR_FIELDS) | cls._INT64_FIELDS | {"vector", "sparse"}
            if (
                description.get("auto_id") is not False
                or description.get("enable_dynamic_field") is not False
                or len(raw_fields) != len(expected_names)
                or set(fields) != expected_names
            ):
                raise ValueError("collection options or fields mismatch")

            for name, max_length in cls._VARCHAR_FIELDS.items():
                field = fields[name]
                if (
                    int(field.get("type", 0)) != cls._VARCHAR_TYPE
                    or int((field.get("params") or {}).get("max_length", 0)) != max_length
                    or field.get("is_primary", False) is not (name == "chunk_id")
                ):
                    raise ValueError(f"varchar field mismatch: {name}")

            for name in cls._INT64_FIELDS:
                field = fields[name]
                if int(field.get("type", 0)) != cls._INT64_TYPE or field.get("is_primary", False) is not False:
                    raise ValueError(f"int64 field mismatch: {name}")

            vector = fields["vector"]
            if (
                int(vector.get("type", 0)) != cls._FLOAT_VECTOR_TYPE
                or int((vector.get("params") or {}).get("dim", 0)) != expected_dimension
                or vector.get("is_primary", False) is not False
            ):
                raise ValueError("vector field mismatch")
            if fields["knowledge_base_id"].get("is_partition_key") is not True:
                raise ValueError("partition key mismatch")
            cls._validate_bm25_schema(description, fields)
        except (AttributeError, TypeError, ValueError, KeyError, SyntaxError) as exc:
            raise KnowledgeVectorError(
                "KNOWLEDGE_VECTOR_SCHEMA_MISMATCH",
                "Milvus collection schema 与知识库配置不一致",
                retryable=False,
            ) from exc

    @classmethod
    def _validate_bm25_schema(cls, description: dict[str, Any], fields: dict[str, Any]) -> None:
        text_params = fields["text"].get("params") or {}
        analyzer_params = text_params.get("analyzer_params")
        if isinstance(analyzer_params, str):
            analyzer_params = json.loads(analyzer_params)
        if str(text_params.get("enable_analyzer", "")).lower() != "true" or analyzer_params != cls.BM25_ANALYZER_PARAMS:
            raise ValueError("text analyzer mismatch")
        sparse = fields["sparse"]
        if int(sparse.get("type", 0)) != cls._SPARSE_FLOAT_VECTOR_TYPE or not sparse.get("is_function_output"):
            raise ValueError("sparse field mismatch")
        functions = description.get("functions") or []
        if len(functions) != 1:
            raise ValueError("bm25 function mismatch")
        function = functions[0]
        if (
            int(function.get("type", 0)) != cls._BM25_FUNCTION_TYPE
            or cls._field_names(function.get("input_field_names")) != ["text"]
            or cls._field_names(function.get("output_field_names")) != ["sparse"]
        ):
            raise ValueError("bm25 function mismatch")

    @staticmethod
    def _field_names(value: Any) -> list[str]:
        # describe_collection 对函数字段名可能返回列表，也可能返回其 repr 字符串。
        if isinstance(value, str):
            value = ast.literal_eval(value)
        return [str(item) for item in value]

    @staticmethod
    def _describe_indexes(client: Any, collection: str) -> list[Any]:
        index_names = client.list_indexes(
            collection_name=collection,
            timeout=settings.MILVUS_TIMEOUT_SECONDS,
        )
        return [
            client.describe_index(
                collection_name=collection,
                index_name=index_name,
                timeout=settings.MILVUS_TIMEOUT_SECONDS,
            )
            for index_name in index_names
        ]

    @classmethod
    def _validate_indexes(
        cls,
        descriptions: Sequence[dict[str, Any]],
        expected_metric: str,
    ) -> None:
        expected = {
            "vector": ("AUTOINDEX", expected_metric.upper()),
            "sparse": ("SPARSE_INVERTED_INDEX", "BM25"),
        }
        try:
            for field_name, (index_type, metric_type) in expected.items():
                indexes = [description for description in descriptions if description.get("field_name") == field_name]
                if (
                    len(indexes) != 1
                    or str(indexes[0].get("index_type", "")).upper() != index_type
                    or str(indexes[0].get("metric_type", "")).upper() != metric_type
                ):
                    raise ValueError(f"index mismatch: {field_name}")
        except (AttributeError, TypeError, ValueError) as exc:
            raise KnowledgeVectorError(
                "KNOWLEDGE_VECTOR_SCHEMA_MISMATCH",
                "Milvus collection 向量索引与知识库配置不一致",
                retryable=False,
            ) from exc

    @classmethod
    def _create_collection(cls, client: Any, name: str, dimension: int, metric: str) -> None:
        from pymilvus import DataType, Function, FunctionType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field(field_name="user_id", datatype=DataType.VARCHAR, max_length=64)
        # 检索总是按所选知识库过滤；分区键让 Milvus 只扫相关分区。
        schema.add_field(
            field_name="knowledge_base_id",
            datatype=DataType.VARCHAR,
            max_length=64,
            is_partition_key=True,
        )
        schema.add_field(field_name="document_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="index_version", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="chunk_ordinal", datatype=DataType.INT64)
        schema.add_field(
            field_name="text",
            datatype=DataType.VARCHAR,
            max_length=65535,
            enable_analyzer=True,
            analyzer_params=cls.BM25_ANALYZER_PARAMS,
        )
        schema.add_field(field_name="filename", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="char_start", datatype=DataType.INT64)
        schema.add_field(field_name="char_end", datatype=DataType.INT64)
        schema.add_field(field_name="page", datatype=DataType.INT64)
        schema.add_field(field_name="section", datatype=DataType.VARCHAR, max_length=120)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dimension)
        schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_function(
            Function(
                name=cls.BM25_FUNCTION_NAME,
                function_type=FunctionType.BM25,
                input_field_names=["text"],
                output_field_names=["sparse"],
            )
        )
        indexes = cls._index_params(client, metric, fields=cls._INDEX_FIELDS)
        client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=indexes,
            consistency_level="Strong",
            timeout=settings.MILVUS_TIMEOUT_SECONDS,
        )

    @classmethod
    def _create_indexes(cls, client: Any, collection: str, metric: str, *, fields: Sequence[str]) -> None:
        client.create_index(
            collection_name=collection,
            index_params=cls._index_params(client, metric, fields=fields),
            timeout=settings.MILVUS_TIMEOUT_SECONDS,
        )

    @staticmethod
    def _index_params(client: Any, metric: str, *, fields: Sequence[str]) -> Any:
        indexes = client.prepare_index_params()
        if "vector" in fields:
            indexes.add_index(field_name="vector", index_type="AUTOINDEX", metric_type=metric)
        if "sparse" in fields:
            indexes.add_index(field_name="sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
        return indexes

    @staticmethod
    def _build_client(profile: EmbeddingProfile | None = None) -> Any:
        # 索引版本落库的 milvus_uri 只是写入时的记录；Milvus 换主机（如 IP 变化）后
        # 旧地址必然不通，所以连接一律走当前配置，database/collection 仍按版本定位数据。
        milvus_uri = settings.MILVUS_CONNECT_URI or settings.MILVUS_URI
        milvus_database = (
            profile.milvus_database if profile is not None and profile.milvus_database else settings.MILVUS_DATABASE
        )
        if not all((milvus_uri, settings.MILVUS_USERNAME, settings.MILVUS_PASSWORD, milvus_database)):
            raise KnowledgeVectorError(
                "KNOWLEDGE_VECTOR_CONFIG_INVALID",
                "Milvus 应用账号配置不完整",
                retryable=False,
            )
        if settings.MILVUS_USERNAME.lower() == "root":
            raise KnowledgeVectorError(
                "KNOWLEDGE_VECTOR_CONFIG_INVALID",
                "知识库禁止使用 Milvus root 账号",
                retryable=False,
            )
        from pymilvus import MilvusClient

        return MilvusClient(
            uri=milvus_uri,
            user=settings.MILVUS_USERNAME,
            password=settings.MILVUS_PASSWORD,
            db_name=milvus_database,
            timeout=settings.MILVUS_TIMEOUT_SECONDS,
        )

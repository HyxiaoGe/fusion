import copy
import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, patch

from app.ai.embeddings.base import EmbeddingProfile
from app.services.knowledge.chunker import KnowledgeChunk
from app.services.knowledge.milvus import (
    KnowledgeVectorError,
    KnowledgeVectorRecord,
    MilvusKnowledgeStore,
)


class MilvusKnowledgeStoreTests(unittest.TestCase):
    @staticmethod
    def _valid_collection_description(dimension=1024):
        varchar_lengths = {
            "chunk_id": 64,
            "user_id": 64,
            "knowledge_base_id": 64,
            "document_id": 64,
            "index_version": 64,
            "text": 65535,
            "filename": 512,
            "section": 120,
        }
        fields = [
            {
                "name": name,
                "type": 21,
                "params": {"max_length": length},
                "is_primary": name == "chunk_id",
            }
            for name, length in varchar_lengths.items()
        ]
        fields.extend(
            {"name": name, "type": 5, "params": {}, "is_primary": False}
            for name in ("chunk_ordinal", "char_start", "char_end", "page")
        )
        fields.append(
            {
                "name": "vector",
                "type": 101,
                "params": {"dim": dimension},
                "is_primary": False,
            }
        )
        return {
            "auto_id": False,
            "enable_dynamic_field": False,
            "fields": fields,
        }

    def test_collection_name_is_dimension_bucketed_and_controlled(self):
        with (
            patch("app.services.knowledge.milvus.settings.MILVUS_COLLECTION_PREFIX", "fusion-knowledge"),
            patch(
                "app.services.knowledge.milvus.settings.KNOWLEDGE_EMBEDDING_ALLOWED_DIMENSIONS",
                "1024",
            ),
        ):
            self.assertEqual(MilvusKnowledgeStore.collection_name(1024), "fusion_knowledge_v2_d1024")
            with self.assertRaises(KnowledgeVectorError):
                MilvusKnowledgeStore.collection_name(768)

    def test_filter_includes_user_bases_and_versions(self):
        expression = MilvusKnowledgeStore.build_search_filter(
            "user-1",
            ["kb-2", "kb-1"],
            ["version-2", "version-1"],
        )

        self.assertIn('user_id == "user-1"', expression)
        self.assertIn('knowledge_base_id in ["kb-1", "kb-2"]', expression)
        self.assertIn('index_version in ["version-1", "version-2"]', expression)

    def test_malformed_search_hit_is_wrapped_as_retryable_vector_error(self):
        invalid_hits = (
            {"id": "chunk-1", "distance": "not-a-number", "entity": {}},
            self._hit_row(char_start=-1),
            self._hit_row(char_start=5, char_end=4),
            self._hit_row(page=-1),
        )

        for row in invalid_hits:
            with self.subTest(row=row), self.assertRaises(KnowledgeVectorError) as raised:
                MilvusKnowledgeStore._hit_from_result(row)
            self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_RESPONSE_INVALID")
            self.assertTrue(raised.exception.retryable)

    def test_root_credentials_are_rejected(self):
        with (
            patch("app.services.knowledge.milvus.settings.MILVUS_URI", "http://milvus:19530"),
            patch("app.services.knowledge.milvus.settings.MILVUS_USERNAME", "root"),
            patch("app.services.knowledge.milvus.settings.MILVUS_PASSWORD", "secret"),
            patch("app.services.knowledge.milvus.settings.MILVUS_DATABASE", "fusion"),
            self.assertRaises(KnowledgeVectorError) as raised,
        ):
            MilvusKnowledgeStore._build_client()

        self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_CONFIG_INVALID")

    def test_persisted_profile_uri_is_record_only_and_connects_current_cluster(self):
        # 版本落库的旧地址（Milvus 换 IP 前写入）不能再用来连接，database 仍按版本定位数据。
        profile = EmbeddingProfile(
            "litellm",
            "embed-v1",
            1024,
            "COSINE",
            "knowledge_v1_d1024",
            "r1",
            "http://historical-milvus:19530",
            "historical_knowledge",
        )
        pymilvus = ModuleType("pymilvus")
        with (
            patch("app.services.knowledge.milvus.settings.MILVUS_URI", "http://current-milvus:19530"),
            patch("app.services.knowledge.milvus.settings.MILVUS_CONNECT_URI", ""),
            patch("app.services.knowledge.milvus.settings.MILVUS_USERNAME", "fusion_app"),
            patch("app.services.knowledge.milvus.settings.MILVUS_PASSWORD", "secret"),
            patch("app.services.knowledge.milvus.settings.MILVUS_TIMEOUT_SECONDS", 17),
            patch.dict(sys.modules, {"pymilvus": pymilvus}),
            patch.object(pymilvus, "MilvusClient", create=True) as client,
        ):
            MilvusKnowledgeStore._build_client(profile)

        client.assert_called_once_with(
            uri="http://current-milvus:19530",
            user="fusion_app",
            password="secret",
            db_name="historical_knowledge",
            timeout=17,
        )

    def test_runtime_connect_uri_routes_current_canonical_cluster(self):
        profile = EmbeddingProfile(
            "litellm",
            "embed-v1",
            1024,
            "COSINE",
            "knowledge_v1_d1024",
            "r1",
            "http://windows-milvus:19530",
            "fusion_knowledge",
        )
        pymilvus = ModuleType("pymilvus")
        with (
            patch("app.services.knowledge.milvus.settings.MILVUS_URI", "http://windows-milvus:19530"),
            patch("app.services.knowledge.milvus.settings.MILVUS_CONNECT_URI", "http://127.0.0.1:19530"),
            patch("app.services.knowledge.milvus.settings.MILVUS_USERNAME", "fusion_app"),
            patch("app.services.knowledge.milvus.settings.MILVUS_PASSWORD", "secret"),
            patch("app.services.knowledge.milvus.settings.MILVUS_TIMEOUT_SECONDS", 17),
            patch.dict(sys.modules, {"pymilvus": pymilvus}),
            patch.object(pymilvus, "MilvusClient", create=True) as client,
        ):
            MilvusKnowledgeStore._build_client(profile)

        client.assert_called_once_with(
            uri="http://127.0.0.1:19530",
            user="fusion_app",
            password="secret",
            db_name="fusion_knowledge",
            timeout=17,
        )

    def test_existing_collection_schema_mismatch_fails_closed(self):
        with self.assertRaises(KnowledgeVectorError) as raised:
            MilvusKnowledgeStore._validate_collection({"fields": []}, 1024)
        self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_SCHEMA_MISMATCH")

    def test_existing_collection_full_schema_contract_is_accepted(self):
        MilvusKnowledgeStore._validate_collection(self._valid_collection_description(), 1024)

    def test_existing_collection_field_contract_mismatches_fail_closed(self):
        cases = {}

        wrong_text_type = self._valid_collection_description()
        next(field for field in wrong_text_type["fields"] if field["name"] == "text")["type"] = 5
        cases["wrong text type"] = wrong_text_type

        short_filename = self._valid_collection_description()
        next(field for field in short_filename["fields"] if field["name"] == "filename")["params"] = {"max_length": 64}
        cases["short filename"] = short_filename

        wrong_primary = self._valid_collection_description()
        next(field for field in wrong_primary["fields"] if field["name"] == "chunk_id")["is_primary"] = False
        cases["wrong primary"] = wrong_primary

        dynamic_schema = self._valid_collection_description()
        dynamic_schema["enable_dynamic_field"] = True
        cases["dynamic fields enabled"] = dynamic_schema

        for name, description in cases.items():
            with self.subTest(name=name), self.assertRaises(KnowledgeVectorError) as raised:
                MilvusKnowledgeStore._validate_collection(copy.deepcopy(description), 1024)
            self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_SCHEMA_MISMATCH")

    def test_existing_collection_vector_index_contract_is_validated(self):
        MilvusKnowledgeStore._validate_indexes(
            [{"field_name": "vector", "index_type": "AUTOINDEX", "metric_type": "COSINE"}],
            "COSINE",
        )

        for invalid in (
            [],
            [{"field_name": "vector", "index_type": "AUTOINDEX", "metric_type": "L2"}],
            [{"field_name": "text", "index_type": "AUTOINDEX", "metric_type": "COSINE"}],
            [{"field_name": "vector", "index_type": "HNSW", "metric_type": "COSINE"}],
        ):
            with self.subTest(invalid=invalid), self.assertRaises(KnowledgeVectorError) as raised:
                MilvusKnowledgeStore._validate_indexes(invalid, "COSINE")
            self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_SCHEMA_MISMATCH")

    @classmethod
    def _valid_hybrid_collection_description(cls, dimension=1024):
        description = cls._valid_collection_description(dimension)
        fields = {field["name"]: field for field in description["fields"]}
        fields["knowledge_base_id"]["is_partition_key"] = True
        fields["text"]["params"].update({"enable_analyzer": "true", "analyzer_params": '{"type":"chinese"}'})
        description["fields"].append(
            {"name": "sparse", "type": 104, "params": {}, "is_primary": False, "is_function_output": True}
        )
        description["functions"] = [
            {
                "name": "text_bm25",
                "type": 1,
                "input_field_names": ["text"],
                "output_field_names": ["sparse"],
            }
        ]
        return description

    def test_schema_version_is_read_from_controlled_collection_name(self):
        self.assertFalse(MilvusKnowledgeStore.is_hybrid_collection("fusion_knowledge_chunks_v1_d1024"))
        self.assertTrue(MilvusKnowledgeStore.is_hybrid_collection("fusion_knowledge_chunks_v2_d1024"))
        self.assertFalse(MilvusKnowledgeStore.is_hybrid_collection("unrelated"))

    def test_index_text_prefixes_heading_path_only_for_hybrid_collections(self):
        chunk = KnowledgeChunk("a" * 64, 0, "两年延保 199 元。", 0, 10, None, "X3 手册 > 售后")

        self.assertEqual(
            MilvusKnowledgeStore.index_text("knowledge_v2_d2", chunk),
            "X3 手册 > 售后\n两年延保 199 元。",
        )
        self.assertEqual(MilvusKnowledgeStore.index_text("knowledge_v1_d2", chunk), "两年延保 199 元。")

    def test_hybrid_collection_schema_contract_is_accepted(self):
        MilvusKnowledgeStore._validate_collection(self._valid_hybrid_collection_description(), 1024, hybrid=True)

    def test_hybrid_collection_schema_mismatches_fail_closed(self):
        cases = {}
        missing_analyzer = self._valid_hybrid_collection_description()
        next(field for field in missing_analyzer["fields"] if field["name"] == "text")["params"].pop("enable_analyzer")
        cases["analyzer disabled"] = missing_analyzer
        wrong_analyzer = self._valid_hybrid_collection_description()
        next(field for field in wrong_analyzer["fields"] if field["name"] == "text")["params"]["analyzer_params"] = (
            '{"type":"english"}'
        )
        cases["analyzer changed"] = wrong_analyzer
        no_partition_key = self._valid_hybrid_collection_description()
        next(field for field in no_partition_key["fields"] if field["name"] == "knowledge_base_id").pop(
            "is_partition_key"
        )
        cases["partition key missing"] = no_partition_key
        no_function = self._valid_hybrid_collection_description()
        no_function["functions"] = []
        cases["bm25 function missing"] = no_function
        wrong_output = self._valid_hybrid_collection_description()
        wrong_output["functions"][0]["output_field_names"] = "['vector']"
        cases["bm25 output changed"] = wrong_output

        for name, description in cases.items():
            with self.subTest(name=name), self.assertRaises(KnowledgeVectorError) as raised:
                MilvusKnowledgeStore._validate_collection(description, 1024, hybrid=True)
            self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_SCHEMA_MISMATCH")

        with self.assertRaises(KnowledgeVectorError):
            MilvusKnowledgeStore._validate_collection(self._valid_hybrid_collection_description(), 1024)

    def test_hybrid_collection_requires_dense_and_bm25_indexes(self):
        dense = {"field_name": "vector", "index_type": "AUTOINDEX", "metric_type": "COSINE"}
        sparse = {"field_name": "sparse", "index_type": "SPARSE_INVERTED_INDEX", "metric_type": "BM25"}
        MilvusKnowledgeStore._validate_indexes([dense, sparse], "COSINE", hybrid=True)

        for invalid in ([dense], [dense, {**sparse, "metric_type": "IP"}]):
            with self.subTest(invalid=invalid), self.assertRaises(KnowledgeVectorError):
                MilvusKnowledgeStore._validate_indexes(invalid, "COSINE", hybrid=True)

    @staticmethod
    def _hit_row(*, similarity=0.8, char_start=0, char_end=7, page=0, suffix="1"):
        return {
            "id": f"chunk-{suffix}",
            "distance": similarity,
            "entity": {
                "document_id": f"doc-{suffix}",
                "knowledge_base_id": "kb-1",
                "index_version": f"version-{suffix}",
                "text": f"chunk text {suffix}",
                "filename": "manual.txt",
                "char_start": char_start,
                "char_end": char_end,
                "page": page,
                "section": "intro",
            },
        }


class MilvusKnowledgeStoreBatchTests(unittest.IsolatedAsyncioTestCase):
    class FakeClient:
        def __init__(self):
            self.upsert_sizes = []
            self.readback_sizes = []

        def upsert(self, *, data, **_kwargs):
            self.upsert_sizes.append(len(data))
            return {"upsert_count": len(data)}

        def get(self, *, ids, **_kwargs):
            self.readback_sizes.append(len(ids))
            return [{"chunk_id": chunk_id} for chunk_id in ids]

        def close(self):
            return None

    async def test_unavailable_error_keeps_underlying_cause_in_logs(self):
        def unreachable():
            raise ConnectionError("No route to host 192.168.1.54:19530")

        store = MilvusKnowledgeStore(client_factory=unreachable)
        with self.assertLogs("app.services.knowledge.milvus", level="WARNING") as logs:
            with self.assertRaises(KnowledgeVectorError) as raised:
                await store.health()

        self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_UNAVAILABLE")
        self.assertIn("ConnectionError: No route to host 192.168.1.54:19530", logs.output[0])

    async def test_upsert_payload_and_strong_readback_are_bounded_by_embedding_batch_size(self):
        client = self.FakeClient()
        store = MilvusKnowledgeStore(client_factory=lambda: client)
        store.ensure_collection = AsyncMock(return_value="knowledge_v1_d2")
        profile = EmbeddingProfile("litellm", "embed-v1", 2, "COSINE", "knowledge_v1_d2", "r1")
        records = [self._record(index) for index in range(5)]

        with patch("app.services.knowledge.milvus.settings.KNOWLEDGE_EMBEDDING_BATCH_SIZE", 2):
            await store.upsert(profile, records)

        store.ensure_collection.assert_awaited_once_with(profile)
        self.assertEqual(client.upsert_sizes, [2, 2, 1])
        self.assertEqual(client.readback_sizes, [2, 2, 1])

    async def test_existing_collection_schema_and_vector_index_are_both_validated(self):
        class ExistingCollectionClient:
            def __init__(self):
                self.closed = False
                self.loaded = []

            def has_collection(self, **_kwargs):
                return True

            def describe_collection(self, **_kwargs):
                return MilvusKnowledgeStoreTests._valid_collection_description(dimension=2)

            def list_indexes(self, **_kwargs):
                return ["vector"]

            def describe_index(self, **_kwargs):
                return {
                    "field_name": "vector",
                    "index_name": "vector",
                    "index_type": "AUTOINDEX",
                    "metric_type": "COSINE",
                }

            def load_collection(self, **kwargs):
                self.loaded.append(kwargs)

            def close(self):
                self.closed = True

        client = ExistingCollectionClient()
        store = MilvusKnowledgeStore(client_factory=lambda: client)
        profile = EmbeddingProfile("litellm", "embed-v1", 2, "COSINE", "knowledge_v1_d2", "r1")

        collection = await store.ensure_collection(profile)

        self.assertEqual(collection, "knowledge_v1_d2")
        self.assertEqual(client.loaded[0]["collection_name"], "knowledge_v1_d2")
        self.assertEqual(client.loaded[0]["replica_number"], 1)
        self.assertTrue(client.closed)

    async def test_prepared_upsert_rejects_collection_from_another_profile(self):
        store = MilvusKnowledgeStore(client_factory=self.FakeClient)
        profile = EmbeddingProfile("litellm", "embed-v1", 2, "COSINE", "knowledge_v1_d2", "r1")

        with self.assertRaises(KnowledgeVectorError) as raised:
            await store.upsert_prepared(profile, "knowledge_v1_d3", [self._record(0)])

        self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_CONFIG_INVALID")
        self.assertFalse(raised.exception.retryable)

    async def test_exact_schema_without_index_is_repaired_before_loading(self):
        class IndexParams:
            def __init__(self):
                self.added = []

            def add_index(self, **kwargs):
                self.added.append(kwargs)

        class MissingIndexClient:
            def __init__(self):
                self.index_created = False
                self.params = IndexParams()
                self.loaded = False

            def has_collection(self, **_kwargs):
                return True

            def describe_collection(self, **_kwargs):
                return MilvusKnowledgeStoreTests._valid_collection_description(dimension=2)

            def list_indexes(self, **_kwargs):
                return ["vector"] if self.index_created else []

            def prepare_index_params(self):
                return self.params

            def create_index(self, **_kwargs):
                self.index_created = True

            def describe_index(self, **_kwargs):
                return {
                    "field_name": "vector",
                    "index_name": "vector",
                    "index_type": "AUTOINDEX",
                    "metric_type": "COSINE",
                }

            def load_collection(self, **_kwargs):
                self.loaded = True

            def close(self):
                return None

        client = MissingIndexClient()
        store = MilvusKnowledgeStore(client_factory=lambda: client)
        profile = EmbeddingProfile("litellm", "embed-v1", 2, "COSINE", "knowledge_v1_d2", "r1")

        await store.ensure_collection(profile)

        self.assertTrue(client.index_created)
        self.assertTrue(client.loaded)
        self.assertEqual(
            client.params.added,
            [{"field_name": "vector", "index_type": "AUTOINDEX", "metric_type": "COSINE"}],
        )

    async def test_exact_schema_with_only_scalar_index_repairs_vector_index(self):
        class IndexParams:
            def __init__(self):
                self.added = []

            def add_index(self, **kwargs):
                self.added.append(kwargs)

        class ScalarIndexOnlyClient:
            def __init__(self):
                self.vector_index_created = False
                self.params = IndexParams()
                self.loaded = False

            def has_collection(self, **_kwargs):
                return True

            def describe_collection(self, **_kwargs):
                return MilvusKnowledgeStoreTests._valid_collection_description(dimension=2)

            def list_indexes(self, **_kwargs):
                names = ["user_id"]
                if self.vector_index_created:
                    names.append("vector")
                return names

            def describe_index(self, *, index_name, **_kwargs):
                if index_name == "user_id":
                    return {
                        "field_name": "user_id",
                        "index_name": "user_id",
                        "index_type": "INVERTED",
                    }
                return {
                    "field_name": "vector",
                    "index_name": "vector",
                    "index_type": "AUTOINDEX",
                    "metric_type": "COSINE",
                }

            def prepare_index_params(self):
                return self.params

            def create_index(self, **_kwargs):
                self.vector_index_created = True

            def load_collection(self, **_kwargs):
                self.loaded = True

            def close(self):
                return None

        client = ScalarIndexOnlyClient()
        store = MilvusKnowledgeStore(client_factory=lambda: client)
        profile = EmbeddingProfile("litellm", "embed-v1", 2, "COSINE", "knowledge_v1_d2", "r1")

        await store.ensure_collection(profile)

        self.assertTrue(client.vector_index_created)
        self.assertTrue(client.loaded)
        self.assertEqual(
            client.params.added,
            [{"field_name": "vector", "index_type": "AUTOINDEX", "metric_type": "COSINE"}],
        )

    async def test_client_close_failure_does_not_override_successful_operation(self):
        class CloseFailureClient:
            def list_collections(self, **_kwargs):
                return ["knowledge_v1_d2"]

            def close(self):
                raise RuntimeError("close failed")

        store = MilvusKnowledgeStore(client_factory=CloseFailureClient)

        await store.health()

    async def test_client_close_failure_does_not_override_operation_failure(self):
        operation_error = RuntimeError("operation failed")

        class DoubleFailureClient:
            def list_collections(self, **_kwargs):
                raise operation_error

            def close(self):
                raise RuntimeError("close failed")

        store = MilvusKnowledgeStore(client_factory=DoubleFailureClient)

        with self.assertRaises(KnowledgeVectorError) as raised:
            await store.health()

        self.assertEqual(raised.exception.code, "KNOWLEDGE_VECTOR_UNAVAILABLE")
        self.assertIs(raised.exception.__cause__, operation_error)

    async def test_search_batches_version_terms_and_merges_global_ranking(self):
        class SearchClient:
            def __init__(self):
                self.filters = []
                self.similarities = iter((0.3, 0.9, 0.6))
                self.loaded = []

            def load_collection(self, **kwargs):
                self.loaded.append(kwargs)

            def search(self, *, filter, **_kwargs):
                self.filters.append(filter)
                suffix = str(len(self.filters))
                return [
                    [
                        MilvusKnowledgeStoreTests._hit_row(
                            similarity=next(self.similarities),
                            suffix=suffix,
                        )
                    ]
                ]

            def close(self):
                return None

        client = SearchClient()
        store = MilvusKnowledgeStore(client_factory=lambda: client)
        profile = EmbeddingProfile("litellm", "embed-v1", 2, "COSINE", "knowledge_v1_d2", "r1")

        with patch("app.services.knowledge.milvus.KNOWLEDGE_MILVUS_FILTER_TERM_BATCH_SIZE", 2):
            hits = await store.search(
                profile=profile,
                query_vector=[1.0, 0.5],
                query_text="question",
                user_id="user-1",
                knowledge_base_ids=["kb-1"],
                index_versions=[f"version-{index}" for index in range(5)],
                limit=2,
            )

        self.assertEqual(len(client.filters), 3)
        self.assertEqual(client.loaded[0]["collection_name"], "knowledge_v1_d2")
        self.assertEqual([expression.count("version-") for expression in client.filters], [2, 2, 1])
        self.assertEqual([hit.similarity for hit in hits], [0.9, 0.6])

    async def test_delete_loads_existing_collection_before_filter_delete(self):
        class DeleteClient:
            def __init__(self):
                self.events = []

            def has_collection(self, **_kwargs):
                return True

            def load_collection(self, **kwargs):
                self.events.append(("load", kwargs))

            def delete(self, **kwargs):
                self.events.append(("delete", kwargs))

            def close(self):
                return None

        client = DeleteClient()
        store = MilvusKnowledgeStore(client_factory=lambda: client)
        profile = EmbeddingProfile("litellm", "embed-v1", 2, "COSINE", "knowledge_v1_d2", "r1")

        await store.delete_index_version(profile, "version-1")

        self.assertEqual([name for name, _kwargs in client.events], ["load", "delete"])
        self.assertEqual(client.events[1][1]["filter"], 'index_version == "version-1"')

    async def test_hybrid_collection_fuses_dense_and_bm25_requests_with_rrf(self):
        from pymilvus import RRFRanker

        class HybridClient:
            def __init__(self):
                self.calls = []

            def load_collection(self, **_kwargs):
                return None

            def search(self, **_kwargs):
                raise AssertionError("v2 collection must not use dense-only search")

            def hybrid_search(self, **kwargs):
                self.calls.append(kwargs)
                return [[MilvusKnowledgeStoreTests._hit_row(similarity=0.03)]]

            def close(self):
                return None

        client = HybridClient()
        store = MilvusKnowledgeStore(client_factory=lambda: client)
        profile = EmbeddingProfile("litellm", "embed-v1", 2, "COSINE", "knowledge_v2_d2", "r1")

        hits = await store.search(
            profile=profile,
            query_vector=[1.0, 0.5],
            query_text="X3 固件 3.7.12",
            user_id="user-1",
            knowledge_base_ids=["kb-1"],
            index_versions=["version-1"],
            limit=4,
        )

        self.assertEqual(len(hits), 1)
        call = client.calls[0]
        self.assertIsInstance(call["ranker"], RRFRanker)
        self.assertEqual(call["limit"], 4)
        dense, sparse = call["reqs"]
        self.assertEqual((dense.anns_field, sparse.anns_field), ("vector", "sparse"))
        self.assertEqual(dense.data, [[1.0, 0.5]])
        self.assertEqual(sparse.data, ["X3 固件 3.7.12"])
        self.assertEqual(sparse.param["metric_type"], "BM25")
        self.assertEqual(dense.expr, sparse.expr)
        self.assertIn('user_id == "user-1"', dense.expr)

    async def test_hybrid_payload_stores_index_text_without_sparse_field(self):
        record = self._record(0)
        record = KnowledgeVectorRecord(
            chunk=KnowledgeChunk(**{**record.chunk.__dict__, "section": "售后"}),
            vector=record.vector,
            user_id=record.user_id,
            knowledge_base_id=record.knowledge_base_id,
            document_id=record.document_id,
            index_version=record.index_version,
            filename=record.filename,
        )

        hybrid = MilvusKnowledgeStore._record_payload(record, hybrid=True)
        legacy = MilvusKnowledgeStore._record_payload(record)

        self.assertEqual(hybrid["text"], "售后\nchunk-0")
        self.assertNotIn("sparse", hybrid)
        self.assertEqual(legacy["text"], "chunk-0")

    @staticmethod
    def _record(index: int) -> KnowledgeVectorRecord:
        chunk = KnowledgeChunk(
            chunk_id=f"{index:064x}",
            ordinal=index,
            text=f"chunk-{index}",
            char_start=index * 10,
            char_end=index * 10 + 7,
            page=None,
            section=None,
        )
        return KnowledgeVectorRecord(
            chunk=chunk,
            vector=[1.0, 0.5],
            user_id="user-1",
            knowledge_base_id="kb-1",
            document_id="doc-1",
            index_version="version-1",
            filename="manual.txt",
        )


if __name__ == "__main__":
    unittest.main()

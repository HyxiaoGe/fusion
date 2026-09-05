"""P0 catalog 完整性契约：声明即消费，缺少消费方必须启动期 fail-fast。"""

import unittest
from unittest.mock import patch


class PromptCatalogConsumerRegistryTests(unittest.TestCase):
    def test_every_catalog_spec_has_a_registered_consumer(self):
        """除显式声明的无消费方条目外，catalog 每一项都必须注册消费方。"""

        from app.core.prompt_catalog import KNOWN_UNCONSUMED_KEYS, PROMPT_SPEC_BY_KEY
        from app.services.prompt_catalog_integrity import verify_prompt_catalog_consumers

        verify_prompt_catalog_consumers()

        from app.core.prompt_catalog import registered_prompt_consumers

        self.assertEqual(set(registered_prompt_consumers()), set(PROMPT_SPEC_BY_KEY) - KNOWN_UNCONSUMED_KEYS)

    def test_declared_unconsumed_key_does_not_silently_pass_as_consumed(self):
        """声明为无消费方的 key 若被注册，说明声明已过期，必须报错而不是放行。"""

        from app.core import prompt_catalog

        consumers = prompt_catalog.registered_prompt_consumers()
        consumers["file_content_enhancement"] = lambda: "x"
        with patch.object(prompt_catalog, "_CONSUMERS", consumers):
            with self.assertRaises(prompt_catalog.PromptCatalogIntegrityError) as ctx:
                prompt_catalog.assert_catalog_fully_consumed()

        self.assertIn("KNOWN_UNCONSUMED_KEYS", str(ctx.exception))

    def test_missing_consumer_fails_fast(self):
        from app.core import prompt_catalog

        consumers = prompt_catalog.registered_prompt_consumers()
        consumers.pop("app_identity")
        with patch.object(prompt_catalog, "_CONSUMERS", consumers):
            with self.assertRaises(prompt_catalog.PromptCatalogIntegrityError) as ctx:
                prompt_catalog.assert_catalog_fully_consumed()

        self.assertIn("app_identity", str(ctx.exception))

    def test_unknown_key_cannot_register(self):
        from app.core import prompt_catalog

        with self.assertRaises(prompt_catalog.PromptCatalogIntegrityError):
            prompt_catalog.register_prompt_consumer("not_a_catalog_key")

    def test_duplicate_registration_is_rejected(self):
        from app.core import prompt_catalog

        with patch.object(prompt_catalog, "_CONSUMERS", {"app_identity": lambda: "first"}):
            with self.assertRaises(prompt_catalog.PromptCatalogIntegrityError):
                prompt_catalog.register_prompt_consumer("app_identity")(lambda: "second")

    def test_catalog_count_is_derived_not_hardcoded(self):
        from app.core.prompt_bundle import PromptBundleValidationError, validate_published_bundle
        from app.core.prompt_catalog import PROMPT_SPECS

        with self.assertRaises(PromptBundleValidationError) as ctx:
            validate_published_bundle(_EmptyBundle())

        self.assertIn(f"{len(PROMPT_SPECS)} 个约定 Prompt", str(ctx.exception))


class _EmptyBundle:
    project_slug = "fusion"
    revision = "a" * 64
    prompts: tuple = ()


if __name__ == "__main__":
    unittest.main()

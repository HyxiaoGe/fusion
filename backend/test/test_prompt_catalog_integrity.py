"""P0 catalog 完整性契约：声明即消费，缺少消费方必须启动期 fail-fast。"""

import unittest
from unittest.mock import patch


class PromptCatalogConsumerRegistryTests(unittest.TestCase):
    def test_every_catalog_spec_has_a_registered_consumer(self):
        """catalog 每一项都必须注册消费方，没有例外集合。"""

        from app.core.prompt_catalog import PROMPT_SPEC_BY_KEY
        from app.services.prompt_catalog_integrity import verify_prompt_catalog_consumers

        verify_prompt_catalog_consumers()

        from app.core.prompt_catalog import registered_prompt_consumers

        self.assertEqual(set(registered_prompt_consumers()), set(PROMPT_SPEC_BY_KEY))

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

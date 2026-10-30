#!/usr/bin/env python3
"""Explicit API selector/persistence checks; no network, UI registration or native load."""
import importlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import test_ai_workflow as base

ai = base.ai
popup = importlib.import_module("narwhal3d.ai_settings_popup")
prefs = importlib.import_module("narwhal3d.preferences")


class SettingsTests(unittest.TestCase):
    def test_existing_selector_exposes_explicit_api_and_preserves_custom_routing(self):
        saved = []
        model = popup.PopupModel(ai.set_provider("openai", "same-model"), anchor=(600, 600),
            window_size=(1200, 900), scale=1, on_save=lambda c:saved.append(c), on_refresh=lambda c:False)
        model.choose_provider("openai_responses")
        config = model.validated_config()
        self.assertEqual(config.provider_id,"openai_responses")
        self.assertEqual(config.model,"same-model")
        self.assertEqual(config.endpoint,"https://api.openai.com/v1")
        self.assertEqual(config.credential_env,"OPENAI_API_KEY")
        model.fields["endpoint"].set_text("http://127.0.0.1:1234/v1")
        model.fields["credential_env"].set_text("LOCAL_TEST_KEY")
        model.choose_provider("openai")
        self.assertEqual(model.validated_config().endpoint,"http://127.0.0.1:1234/v1")
        self.assertEqual(model.validated_config().credential_env,"LOCAL_TEST_KEY")
        self.assertEqual(saved,[])

    def test_public_settings_round_trip_retains_choice_never_raw_key(self):
        with tempfile.TemporaryDirectory(prefix="narwhal-responses-settings-") as tmp, \
             patch.object(prefs,"settings_path",lambda:Path(tmp)/"settings.json"), \
             patch.object(prefs,"_cache",{}), patch.object(prefs,"_cache_path",None):
            prefs.update({"ai_config":{"provider_id":"openai_responses","model":"same-model",
                          "endpoint":"https://api.openai.com/v1","credential_env":"TEST_KEY",
                          "allow_insecure_remote":False,"api_key":"private-fixture-key"}})
            config=prefs.load()["ai_config"]
            self.assertEqual(config["provider_id"],"openai_responses")
            self.assertEqual(config["credential_env"],"TEST_KEY")
            self.assertNotIn("api_key", set(config))
            self.assertNotIn("private-fixture-key",(Path(tmp)/"settings.json").read_text())
            self.assertEqual(ai.set_provider(**config).provider_id,"openai_responses")

    def test_legacy_chat_cannot_silently_route_responses(self):
        config=ai.set_provider("openai_responses","same-model")
        with self.assertRaisesRegex(base.http.TransportError,"native workflow"):
            base.http.send_chat(config,{"messages":[]},{},opener=lambda *_a,**_k:self.fail("network attempted"))


if __name__=="__main__":
    unittest.main(verbosity=2)

"""Verify artifact rejection, pixel mapping and runtime/session ownership."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import weakref

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from ctrl_loop.local_detector import LocalDetector, verified_model


class LocalDetectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "dependencies").mkdir()
        lock_bytes = (ROOT / "dependencies/ui-detector.lock.json").read_bytes()
        (self.root / "dependencies/ui-detector.lock.json").write_bytes(lock_bytes)
        lock = json.loads(lock_bytes)
        self.model = self.root / ".runtime/ui-detector/model.onnx"
        self.model.parent.mkdir(parents=True)
        self.model.write_bytes(b"test-only model bytes; native runtime is mocked")
        self.manifest = {
            "lockfileSha256": hashlib.sha256(lock_bytes).hexdigest(),
            "checkpointSha256": next(x["sha256"] for x in lock["artifacts"] if x["role"] == "checkpoint"),
            "modelId": lock["id"], "source": lock["source"], "export": lock["export"],
            "tensorContract": lock["tensorContract"], "names": {"0": "icon"},
            "onnx": {"sha256": hashlib.sha256(self.model.read_bytes()).hexdigest(),
                     "sizeBytes": self.model.stat().st_size, "path": "Z:/unrelated/export/model.onnx",
                     "outputs": [{"name": "output0", "shape": [1, 5, 33600]}]},
        }
        self.write_manifest()
        self.events = []
        self.session_refs = []
        owner = self

        class Session:
            def get_inputs(self):
                return [NS(name="images", shape=[1, 3, 1280, 1280], type="tensor(float)")]
            def get_outputs(self):
                return [NS(name="output0", shape=[1, 5, 33600], type="tensor(float)")]
            def run(self, outputs, feed):
                owner.events.append(("run", outputs, feed["images"].shape))
                # Original [10,20,110,80], 400x200 image letterboxed to 1280.
                return [np.array([[[192], [480], [320], [192], [.9]]], dtype=np.float32)]

        class Runtime:
            def __enter__(self):
                owner.events.append("runtime-open")
                return self
            def cpu_session(self, path):
                owner.events.append(("session", path))
                session = Session()
                owner.session_refs.append(weakref.ref(session))
                return session
            def __exit__(self, *args):
                owner.events.append(("runtime-close", all(ref() is None for ref in owner.session_refs)))

        self.runtime_class = Runtime
        self.session_class = Session
        self.runtime_mock = self.enterContext(patch("ctrl_loop.local_detector.WindowsMLRuntime", Runtime))

    def write_manifest(self):
        self.model.with_name("export-manifest.json").write_text(json.dumps(self.manifest))

    def test_persistent_session_original_pixel_boxes_and_release_before_runtime(self):
        rgb = np.full((200, 400, 3), 41, dtype=np.uint8)
        with LocalDetector(self.root) as detector:
            np.testing.assert_allclose(detector.detect(rgb), [[10, 20, 110, 80]])
            detector.detect(rgb)
            np.testing.assert_array_equal(rgb, np.full_like(rgb, 41))
        self.assertEqual(self.events.count(("session", self.model)), 1)
        self.assertEqual(sum(isinstance(event, tuple) and event[0] == "run" for event in self.events), 2)
        self.assertEqual(self.events[-1], ("runtime-close", True))
        with self.assertRaisesRegex(RuntimeError, "Enter"):
            detector.detect(rgb)

    def test_tampered_model_rejected_before_native_runtime_opens(self):
        self.model.write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            with LocalDetector(self.root):
                self.fail("must not load")
        self.assertEqual(self.events, [])

    def test_wrong_provenance_rejected_even_when_model_hash_matches(self):
        self.manifest["source"]["revision"] = "different-checkpoint"
        self.write_manifest()
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            verified_model(self.root)
        self.assertEqual(self.events, [])

    def test_bad_tensor_contract_releases_session_on_initialization_failure(self):
        with patch.object(self.session_class, "get_inputs", return_value=[NS(
            name="images", shape=[1, 3, 640, 640], type="tensor(float)",
        )]):
            with self.assertRaisesRegex(RuntimeError, "tensor contract"):
                with LocalDetector(self.root):
                    self.fail("must not load")
        self.assertEqual(self.events[-1], ("runtime-close", True))


if __name__ == "__main__":
    unittest.main()

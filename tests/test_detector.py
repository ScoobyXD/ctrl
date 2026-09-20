import unittest
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ctrl_loop.detector import decode, preprocess


class DetectorGeometryTests(unittest.TestCase):
    def test_letterbox_preserves_rgb_and_records_asymmetric_padding(self):
        image = np.zeros((201, 400, 3), dtype=np.uint8)
        image[:, :, 0] = 255
        tensor, transform = preprocess(image, 640)
        self.assertEqual(tensor.shape, (1, 3, 640, 640))
        self.assertEqual(transform.resized_height, 322)
        self.assertEqual(transform.pad_top, 159)
        np.testing.assert_array_equal(tensor[0, :, 320, 320], [1, 0, 0])
        np.testing.assert_allclose(tensor[0, :, 0, 0], np.full(3, 114 / 255))

    def test_inverse_transform_nms_and_clipping(self):
        _, transform = preprocess(np.zeros((200, 400, 3), dtype=np.uint8), 640)
        # Original box [10,20,110,80] becomes center [96,240], size [160,96].
        raw = np.array([[[96, 96, 640], [240, 240, 320], [160, 160, 100],
                         [96, 96, 100], [.9, .8, .7]]], dtype=np.float32)
        result = decode(raw, transform)
        self.assertEqual(len(result), 2)
        np.testing.assert_allclose(result[0]["box_xyxy"], [10, 20, 110, 80])
        self.assertEqual(result[1]["box_xyxy"][2], 400)

    def test_empty_and_invalid_output(self):
        _, transform = preprocess(np.zeros((20, 20, 3), dtype=np.uint8), 32)
        self.assertEqual(decode(np.zeros((1, 5, 10)), transform), [])
        for bad in (np.zeros((1, 6, 10)), np.full((1, 5, 1), np.nan)):
            with self.assertRaises(ValueError):
                decode(bad, transform)


if __name__ == "__main__":
    unittest.main()

"""Portable-path and student-guard tests. No C/D-drive files are created."""
import unittest
from storage_policy import validate_storage


class StorageTests(unittest.TestCase):
    def test_public_windows_without_e(self):
        validate_storage([r"D:\Marker\private"], {}, "nt")

    def test_public_linux(self):
        validate_storage(["/home/marker/private"], {}, "posix")

    def test_public_macos(self):
        validate_storage(["/Users/marker/private"], {}, "posix")

    def environment(self):
        return {"BDF_REQUIRE_E_STORAGE": "1", **{k: r"E:\workspace\cache" for k in
                ("TEMP", "TMP", "MPLCONFIGDIR", "PIP_CACHE_DIR")}}

    def test_student_e_allowed(self):
        validate_storage([r"E:\workspace\out"], self.environment(), "nt")

    def test_student_c_rejected(self):
        with self.assertRaises(ValueError):
            validate_storage([r"C:\out"], self.environment(), "nt")

    def test_student_wrong_cache_rejected(self):
        for key in ("TEMP", "TMP", "MPLCONFIGDIR", "PIP_CACHE_DIR"):
            env = self.environment(); env[key] = r"C:\cache"
            with self.subTest(cache=key), self.assertRaises(ValueError):
                validate_storage([r"E:\out"], env, "nt")

    def test_student_relative_drive_rejected(self):
        with self.assertRaises(ValueError):
            validate_storage(["E:relative"], self.environment(), "nt")


if __name__ == "__main__":
    unittest.main(verbosity=2)

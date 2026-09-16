"""Checkout setup regressions: privacy, offline reuse and interrupted downloads."""
from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from scripts import setup_assets

APP = Path(__file__).resolve().parents[2]


class Download(io.BytesIO):
    def __init__(self, data=b"approved", url="https://github.com/example/asset"):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url


def asset(data=b"approved"):
    return {"path":"models/adetailer/face.pt", "bytes":len(data),
            "sha256":hashlib.sha256(data).hexdigest()}


class AssetInstallTests(unittest.TestCase):
    def test_verified_download_is_published_and_reused_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertTrue(setup_assets.install_asset(root,asset(),opener=lambda *a,**k:Download()))
            with patch.object(setup_assets,"urlopen") as network:
                self.assertFalse(setup_assets.install_asset(root,asset()))
                network.assert_not_called()
            self.assertEqual(b"approved",(root/asset()["path"]).read_bytes())
            self.assertFalse(list(root.rglob("*.download")))

    def test_wrong_short_and_oversized_downloads_are_never_models(self):
        for data in (b"changed!",b"short",b"approved and excess"):
            with self.subTest(data=data),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                with self.assertRaisesRegex(ValueError,"mismatch|larger"):
                    setup_assets.install_asset(root,asset(),opener=lambda *a,**k:Download(data))
                self.assertEqual([], [p for p in root.rglob("*") if p.is_file()])

    def test_interrupted_download_is_removed_before_retry(self):
        class Interrupted(Download):
            def read(self,*args):
                raise OSError("connection interrupted")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(OSError,"interrupted"):
                setup_assets.install_asset(root,asset(),opener=lambda *a,**k:Interrupted())
            self.assertFalse(list(root.rglob("*.download")))
            self.assertTrue(setup_assets.install_asset(root,asset(),opener=lambda *a,**k:Download()))

    def test_existing_different_model_is_preserved_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/asset()["path"];target.parent.mkdir(parents=True)
            target.write_bytes(b"user model")
            with patch.object(setup_assets,"urlopen") as network:
                with self.assertRaisesRegex(ValueError,"preserved"):
                    setup_assets.install_asset(root,asset())
                network.assert_not_called()
            self.assertEqual(b"user model",target.read_bytes())

    def test_concurrent_user_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);target=root/asset()["path"]
            def opener(*args,**kwargs):
                target.write_bytes(b"arrived during download")
                return Download()
            with self.assertRaisesRegex(ValueError,"preserved"):
                setup_assets.install_asset(root,asset(),opener=opener)
            self.assertEqual(b"arrived during download",target.read_bytes())
            self.assertFalse(list(root.rglob("*.download")))

    def test_http_redirect_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,"HTTPS"):
                setup_assets.install_asset(Path(tmp),asset(),opener=lambda *a,**k:Download(url="http://example.invalid/a"))

    def test_offline_cache_requires_the_same_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cache=root/"cache";cache.mkdir()
            (cache/"face.pt").write_bytes(b"approved")
            with patch.object(setup_assets,"urlopen") as network:
                self.assertTrue(setup_assets.install_asset(root/"app",asset(),from_directory=cache))
                network.assert_not_called()

    def test_unsafe_manifest_paths_cannot_write_or_download(self):
        for name in ("../escape.pt","/models/a.pt","models/../../escape.pt","models/C:/a.pt","models\\adetailer\\a.pt","elsewhere/dir/a.pt"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);spec=asset();spec["path"]=name
                with patch.object(setup_assets,"urlopen") as network:
                    with self.assertRaises(ValueError):setup_assets.install_asset(root,spec)
                    network.assert_not_called()
                self.assertEqual([],list(root.iterdir()))

    def test_linked_models_directory_cannot_escape_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=root/"app";other=root/"other";app.mkdir();other.mkdir()
            try:(app/"models").symlink_to(other,target_is_directory=True)
            except OSError as exc:self.skipTest(f"Symlink creation unavailable: {exc}")
            with self.assertRaises(ValueError):setup_assets.destination(app,asset()["path"])
            self.assertEqual([],list(other.iterdir()))


class CheckoutLauncherTests(unittest.TestCase):
    def launcher_module(self):
        spec=importlib.util.spec_from_file_location("checkout_test",APP/"start_studio.py")
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def test_config_and_state_are_outside_clone_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)/"Clone With Spaces";repo.mkdir()
            for name in ("packaging/windows/start_studio.py","docs/studio/internal-alpha/studio-config.template.json"):
                target=repo/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(APP/name,target)
            launcher=self.launcher_module().checkout_launcher(repo)
            self.assertEqual(repo/"venv/Scripts/python.exe",launcher.PYTHON)
            self.assertEqual(repo/"launch_studio.py",launcher.LAUNCHER)
            self.assertFalse(launcher.ROOT.is_relative_to(repo))
            launcher.ROOT.mkdir()
            self.assertEqual(0,launcher.bootstrap_config())
            before=launcher.CONFIG.read_bytes();config=json.loads(before)
            self.assertEqual({},config["model_roots"])
            self.assertEqual(str(launcher.ROOT/"Studio-State"),config["studio_state_root"])
            self.assertEqual(0,launcher.bootstrap_config())
            self.assertEqual(before,launcher.CONFIG.read_bytes())

    def test_source_launcher_creates_only_the_expected_empty_model_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/"source";repo.mkdir();python=repo/"venv/Scripts/python.exe"
            python.parent.mkdir(parents=True);python.touch()
            module=self.launcher_module()
            from types import SimpleNamespace
            launcher=SimpleNamespace(ROOT=root/"source-data",PYTHON=python,main=lambda:17)
            with patch.object(module,"APP",repo),patch.object(module,"checkout_launcher",return_value=launcher):
                self.assertEqual(17,module.main())
            self.assertEqual({"Stable-diffusion","VAE","text_encoder"},{p.name for p in (repo/"models").iterdir()})
            self.assertTrue(all(not list(p.iterdir()) for p in (repo/"models").iterdir()))


if __name__=="__main__":
    unittest.main()

"""Synthetic Rust Library store, packaging and adversarial-input contracts."""
import importlib.util
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('library_adapter', ROOT/'narwhal3d/native_library.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class LibraryTests(unittest.TestCase):
    def test_refresh_thumbnail_preserves_source_and_rejects_invalid_png(self):
        asset = self.asset()
        source = Path(asset['source_file']).read_bytes()
        previous = Path(asset['thumbnail'])
        updated = self.store.refresh_thumbnail(asset['id'], previous)
        self.assertEqual(updated['id'], asset['id'])
        self.assertEqual(updated['source_file'], asset['source_file'])
        self.assertEqual(Path(updated['source_file']).read_bytes(), source)
        self.assertNotEqual(updated['thumbnail'], asset['thumbnail'])
        self.assertTrue(previous.exists(), 'old immutable thumbnail remains safe for open readers')
        before = self.store.catalog()
        with self.assertRaises(api.LibraryError):
            self.store.refresh_thumbnail(asset['id'], self.source)
        self.assertEqual(self.store.catalog(), before)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='narwhal-library-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = api.LibraryStore(self.root/'library')
        self.source = self.root/'source.narwhal'
        self.source.write_bytes(b'exact geometry opaque source fixture')
        self.thumb = self.root/'preview.png'
        self.thumb.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))

    def asset(self, store=None):
        return (store or self.store).add_asset('Cylinder', 'uncategorized', self.source, self.thumb, ['part'])

    def test_crud_and_restart(self):
        self.assertEqual(self.store.catalog()['assets'], [])
        cat = self.store.create_category('Fixtures')
        asset = self.asset()
        self.store.move_asset(asset['id'], cat['id'])
        self.store.rename_asset(asset['id'], 'Bushing')
        self.assertEqual(self.store.catalog('fixtures')['assets'][0]['name'], 'Bushing')
        self.assertEqual(self.store.catalog('part')['assets'][0]['id'], asset['id'])
        self.assertEqual(api.LibraryStore(self.store.root).catalog()['assets'][0]['id'], asset['id'])
        self.store.delete_category(cat['id'])
        self.assertEqual(self.store.catalog()['assets'][0]['category_id'], 'uncategorized')
        copied = Path(asset['source_file']).read_bytes()
        self.store.remove_asset(asset['id'])
        self.assertEqual(self.store.catalog()['assets'], [])
        self.assertEqual(copied, self.source.read_bytes())
        self.assertTrue(Path(asset['source_file']).is_file(), 'immutable bytes retained for open readers')

    def test_package_roundtrip_merge_replace(self):
        self.asset()
        package = self.root/'shared.narwhallib'
        self.store.export_library(package)
        with zipfile.ZipFile(package) as z:
            self.assertIn('manifest.json', z.namelist())
        target = api.LibraryStore(self.root/'target')
        target.import_library(package)
        target.import_library(package)
        entries = target.catalog()['assets']
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(entries[0]['id'], entries[1]['id'])
        target.import_library(package, 'replace')
        entries = target.catalog()['assets']
        self.assertEqual(len(entries), 1)
        self.assertEqual(Path(entries[0]['source_file']).read_bytes(), self.source.read_bytes())

    def test_invalid_packages_leave_catalogue_unchanged(self):
        self.asset()
        before = self.store.catalog()
        good = self.root/'good.narwhallib'
        self.store.export_library(good)
        with zipfile.ZipFile(good) as z:
            contents = {n:z.read(n) for n in z.namelist()}
        manifest = json.loads(contents['manifest.json'])
        for mode in ('merge', 'replace'):
            for attack in ('traversal', 'duplicate', 'checksum', 'version', 'symlink', 'extra', 'preview'):
                bad = self.root/f'{mode}-{attack}.narwhallib'
                entries = dict(contents)
                if attack=='checksum': entries[manifest['assets'][0]['source_file']] = b'corrupt'
                if attack=='preview':
                    modified = json.loads(contents['manifest.json'])
                    item=modified['assets'][0]
                    entries[item['thumbnail']]=b'invalid png'
                    import hashlib
                    item['thumbnail_sha256']=hashlib.sha256(entries[item['thumbnail']]).hexdigest()
                    entries['manifest.json']=json.dumps(modified).encode()
                if attack=='version':
                    changed = dict(manifest, version=99)
                    entries['manifest.json'] = json.dumps(changed).encode()
                with zipfile.ZipFile(bad, 'w') as z:
                    for name,data in entries.items(): z.writestr(name, data)
                    if attack=='traversal': z.writestr('../escape', b'bad')
                    if attack=='extra': z.writestr('assets/unreferenced.narwhal', b'extra')
                    if attack=='duplicate': z.writestr('manifest.json', entries['manifest.json'])
                    if attack=='symlink':
                        info=zipfile.ZipInfo('assets/linked.narwhal')
                        info.create_system=3
                        info.external_attr=0o120777 << 16
                        z.writestr(info, '/etc/passwd')
                with self.assertRaises(api.LibraryError, msg=attack):
                    self.store.import_library(bad, mode)
                self.assertEqual(self.store.catalog(), before)
        self.assertFalse((self.root/'escape').exists())

    def test_validation(self):
        for name in ('', '../\n', 'x'*257):
            with self.assertRaises(api.LibraryError): self.store.create_category(name)
        with self.assertRaises(api.LibraryError): self.store.delete_category('uncategorized')
        with self.assertRaises(api.LibraryError): self.store.add_asset('Bad', 'missing', self.source, self.thumb)
        self.store.create_category('A')
        with self.assertRaises(api.LibraryError): self.store.create_category('a')
        self.assertEqual(self.store.catalog()['assets'], [])
        protected=self.root/'important.py'
        protected.write_bytes(b'original source')
        with self.assertRaises(api.LibraryError): self.store.export_library(protected)
        self.assertEqual(protected.read_bytes(),b'original source')
        repository=self.root/'repository'
        repository.mkdir(); (repository/'.git').mkdir()
        with self.assertRaises(api.LibraryError): api.LibraryStore(repository).catalog()
        self.assertFalse((repository/'assets').exists())

    def test_layout(self):
        for width,height in ((200,700),(400,900),(130,500)):
            plan=self.store.layout(width,height,1.,0.,500)
            self.assertLess(len(plan['tiles']), 30)
            for tile in plan['tiles']:
                x,y,x1,y1=tile['art']
                self.assertAlmostEqual(x1-x,y1-y)
            end=self.store.layout(width,height,1.,1e9,500)
            self.assertEqual(end['scroll'], end['max_scroll'])


if __name__=='__main__':
    unittest.main()

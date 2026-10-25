"""Bound composition fixtures while retaining real runtime packages and hash checks."""
import importlib.util
import io
from pathlib import Path
import tarfile
from unittest.mock import patch


def omit_native_parts(directory, names):
    return ['source'] if Path(directory).name == 'botty-native' and 'source' in names else []


def compact_native_sources(package):
    # These tests exercise composition, not rebuilding the emulator. The actual
    # complete source delivery is independently checked by portal-manifest.py.
    spec = importlib.util.spec_from_file_location('fixture_source_parts',
        Path(__file__).resolve().parents[1] / 'scripts/source_parts.py')
    sources = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sources)
    archive = package / 'botty-native-source.tar.gz'
    data = b'Composition fixture source archive\n'
    with tarfile.open(archive, 'w:gz') as tar:
        entry = tarfile.TarInfo('botty-native/README.txt')
        entry.size = len(data)
        tar.addfile(entry, io.BytesIO(data))
    with patch.object(sources, 'PART_SIZE', 64):
        sources.publish(archive, package)

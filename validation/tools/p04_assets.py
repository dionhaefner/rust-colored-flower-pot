"""Audit embedded P04 assets against the unchanged frozen reference (offline)."""
import hashlib
from pathlib import Path
from terracotta.server import create_app
from common import ROOT, COMMIT, write_json
source=ROOT/'.cache/reference/terracotta'
records=[]
for asset in sorted((ROOT/'src/assets/cmaps').glob('*.npy')):
    original=source/'cmaps/data'/asset.name
    assert asset.read_bytes()==original.read_bytes()
    records.append({'path':str(asset.relative_to(ROOT)),'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),'source':str(original.relative_to(ROOT/'.cache/reference'))})
for asset in sorted((ROOT/'src/assets/static').iterdir()):
    original=source/'server/static'/asset.name
    assert asset.read_bytes()==original.read_bytes()
    records.append({'path':str(asset.relative_to(ROOT)),'sha256':hashlib.sha256(asset.read_bytes()).hexdigest(),'source':str(original.relative_to(ROOT/'.cache/reference'))})
client=create_app().test_client()
for route,asset in [('/swagger.json','swagger.json'),('/apidoc','apidoc.html')]:
    generated=client.get(route).data;path=ROOT/'src/assets'/asset
    assert generated==path.read_bytes()
    records.append({'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(generated).hexdigest(),'source_route':route})
write_json(ROOT/'validation/reference/p04-assets.json',{'reference_commit':COMMIT,'records':records})
print('verified',len(records),'embedded assets')

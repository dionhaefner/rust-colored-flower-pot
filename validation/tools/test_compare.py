"""Adversarial comparator tests use captured real pixels, never synthetic scenes."""
import copy
import json
from pathlib import Path
import shutil
import numpy as np
from PIL import Image
import pytest
from compare import compare, structured
from common import ROOT, read_json, write_json

GOLDEN=ROOT/'validation/reference/capture'

@pytest.fixture
def candidate(tmp_path):
    shutil.copytree(GOLDEN,tmp_path/'candidate')
    return tmp_path/'candidate'


def test_self():
    assert compare(GOLDEN,GOLDEN)['passed']


@pytest.mark.parametrize('mutation',['status','missing_key','key_order','list_order','storage','type','membership'])
def test_structured_corruption(candidate,mutation):
    path=candidate/'capture.json'; obj=read_json(path)
    if mutation=='status': obj['contract']['requests'][0]['status']=500
    elif mutation=='missing_key': del obj['contract']['requests'][0]['json']['keys']
    elif mutation=='key_order': obj['contract']['settings']=dict(reversed(list(obj['contract']['settings'].items())))
    elif mutation=='list_order': obj['contract']['requests'][0]['json']['keys'].reverse()
    elif mutation=='storage': obj['contract']['storage'][-1]['rows'][0][0]='altered-storage'
    elif mutation=='type': obj['contract']['requests'][0]['status']=200.0
    elif mutation=='membership': obj['images'].pop()
    write_json(path,obj)
    assert not compare(GOLDEN,candidate)['passed'],mutation


@pytest.mark.parametrize('mutation',['dimensions','mask','pixels','shift','seam','categories','missing'])
def test_image_corruption(candidate,mutation):
    name='landcover-preview' if mutation=='categories' else 'rgb'
    path=candidate/f'images/{name}.png'
    with Image.open(path) as im: arr=np.asarray(im.convert('RGBA')).copy()
    if mutation=='dimensions': arr=arr[:128]
    elif mutation=='mask': arr[100:120,180:200,3]=0
    elif mutation=='pixels': arr[100:120,180:200,:3]=[255,0,255]
    elif mutation=='shift': arr=np.roll(arr,8,axis=1)
    elif mutation=='seam': arr[:,250:253,:]=[255,0,255,255]
    elif mutation=='categories': arr[100:120,100:120,:]=[255,0,255,255]
    elif mutation=='missing': path.unlink()
    if mutation!='missing': Image.fromarray(arr).save(path)
    assert not compare(GOLDEN,candidate)['passed'],mutation


@pytest.mark.parametrize('field',['values','mask'])
def test_array_corruption(candidate,field):
    path=candidate/'arrays/landcover.npz'
    with np.load(path) as data: arrays={k:data[k].copy() for k in data.files}
    if field=='mask': arrays[field][0,0]=not arrays[field][0,0]
    else: arrays[field][0,0]+=1
    np.savez_compressed(path,**arrays)
    assert not compare(GOLDEN,candidate)['passed']


def test_compressed_encoding_is_not_contract(candidate):
    path=candidate/'images/rgb.png'
    with Image.open(path) as im: rgba=im.convert('RGBA')
    rgba.save(path,compress_level=9)
    assert compare(GOLDEN,candidate)['passed']


def test_no_numeric_type_coercion():
    assert structured({'value':1},{'value':True})
    assert structured({'value':1},{'value':1.0})

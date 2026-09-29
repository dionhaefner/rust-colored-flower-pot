"""Actual Chromium exploration of unchanged assets, with offline basemap suppression."""
import json, os, subprocess, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[3]
OUT=Path(os.environ.get('TC_BROWSER_OUT',ROOT/'process/validation/evidence/p07/browser'));OUT.mkdir(parents=True,exist_ok=True)
BIN=Path(os.environ.get('TC_BROWSER_BINARY',ROOT/'target/debug/terracotta')).resolve()
DB=ROOT/'process/validation/artifacts/capture-reproduction/reference.sqlite'
QUEUE=os.environ.get('TC_BROWSER_QUEUE','64')

def wait(url):
    for _ in range(100):
        try:
            urllib.request.urlopen(url).close(); return
        except OSError:time.sleep(.1)
    raise RuntimeError(url)

with (OUT/'services.log').open('w') as log:
    backend=subprocess.Popen([str(BIN),'serve','-d',str(DB),'--port','5077'],cwd=ROOT,stdout=log,stderr=log,env=dict(os.environ,TC_RASTER_QUEUE_CAPACITY=QUEUE))
    client=None
    try:
        wait('http://127.0.0.1:5077/keys')
        client=subprocess.Popen([str(BIN),'connect','127.0.0.1:5077','--port','5177','--no-browser'],cwd=ROOT,stdout=log,stderr=log)
        wait('http://127.0.0.1:5177/')
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            responses=[];errors=[]
            page.on('response',lambda r:responses.append({'url':r.url,'status':r.status}) if '127.0.0.1:5077' in r.url else None)
            page.on('pageerror',lambda e:errors.append(str(e)))
            # Basemap is unrelated third-party traffic, kept offline; native data is real.
            def route_request(route):
                url=route.request.url
                if url.startswith(('http://127.0.0.1:','data:','blob:')):
                    route.continue_()
                elif 'api.mapbox.com/styles/v1/' in url:
                    route.fulfill(json={'version':8,'sources':{},'layers':[{'id':'offline-background','type':'background','paint':{'background-color':'#e9edf0'}}]})
                else:
                    route.abort()
            page.route('**/*',route_request)
            page.goto('http://127.0.0.1:5177/')
            page.wait_for_timeout(3500)
            (OUT/'initial.txt').write_text(page.locator('body').inner_text())
            page.screenshot(path=str(OUT/'initial.png'))
            print(page.locator('body').inner_text())
            print('inputs',page.locator('input').evaluate_all('(els)=>els.map(e=>({placeholder:e.placeholder,role:e.getAttribute("role"),type:e.type}))'))
            row=page.get_by_role('row').filter(has_text='lagoon').filter(has_text='red').first
            row.click()
            page.wait_for_timeout(4500)
            (OUT/'selected.txt').write_text(page.locator('body').inner_text())
            page.screenshot(path=str(OUT/'singleband.png'))
            print('selected',page.locator('body').inner_text())
            assert any('/preview.png' in r['url'] and r['status']==200 for r in responses)
            assert any('/singleband/' in r['url'] and '/preview.png' not in r['url'] and r['status']==200 for r in responses)
            assert any('/metadata/' in r['url'] and r['status']==200 for r in responses)
            page.get_by_text('singleband',exact=True).click()
            page.get_by_role('option',name='rgb',exact=True).click()
            page.wait_for_timeout(1000)
            print('rgb controls',page.locator('body').inner_text())
            print('buttons',page.get_by_role('button').all_text_contents())
            page.screenshot(path=str(OUT/'rgb-initial.png'))
            selects=page.locator('div[role="button"][aria-haspopup="listbox"]')
            for index,band in enumerate(['red','green','blue'],1):
                selects.nth(index).click()
                page.get_by_role('option',name=band,exact=True).click()
            page.wait_for_timeout(4000)
            page.screenshot(path=str(OUT/'rgb.png'))
            assert any('/rgb/' in r['url'] and r['status']==200 for r in responses)
            with page.expect_popup() as opened:
                page.get_by_role('link',name='View full metadata').click()
            metadata=opened.value
            metadata.wait_for_load_state()
            metadata.wait_for_timeout(300)
            (OUT/'metadata-page.txt').write_text(metadata.content()+'\nURL: '+metadata.url)
            data=json.loads(metadata.locator('pre').text_content())
            assert 'bounds' in data and 'percentiles' in data
            (OUT/'metadata.json').write_text(json.dumps(data,indent=2))
            assert not errors, errors
            failures=[r for r in responses if r['status']>=400]
            (OUT/'report.json').write_text(json.dumps({'passed':not failures,'responses':responses,'errors':errors,'failed_responses':failures,'queue_capacity':int(QUEUE),'basemap':'Test-only offline background style; unchanged client assets and real raster/metadata service','browser':browser.version},indent=2))
            assert not failures, failures
            browser.close()
    finally:
        if client:client.terminate();client.wait(timeout=10)
        backend.terminate();backend.wait(timeout=10)

"""Non-destructive engine checks against the synthetic server on 8792 only.

Requires green_e2e_postgres.py in serve mode. Never starts missions or checks out.
"""
import json
import re
import shutil
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright, expect
from green_browser_checks import BASE, login


def assert_fit(page, route, width):
    overflow=page.evaluate('''() => ({width:innerWidth,scroll:document.documentElement.scrollWidth,
        elements:[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth+1)
        .slice(0,8).map(el=>({tag:el.tagName,classes:el.className,right:el.getBoundingClientRect().right}))})''')
    if overflow['scroll']>overflow['width']+1:
        overflow['isolation']=page.evaluate('''() => [...document.querySelectorAll('main>section,header,footer')].map(el=>{
            const old=el.style.display;el.style.display='none';const scroll=document.documentElement.scrollWidth;
            el.style.display=old;return {classes:el.className,scroll};})''')
    assert overflow['scroll']<=overflow['width']+1, (route,width,overflow)


def main():
    with sync_playwright() as engines:
        firefox_paths=sorted((Path.home()/'.cache/ms-playwright').glob('firefox-*/firefox/firefox'))
        assert firefox_paths, 'Firefox is unavailable; do not claim engine coverage.'
        for name,kind,path in [('Chromium',engines.chromium,shutil.which('chromium')),
                               ('Firefox',engines.firefox,str(firefox_paths[-1]))]:
            browser=kind.launch(headless=True,executable_path=path)
            context=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
            page=context.new_page()
            errors=[]
            failed_assets=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('response',lambda response:failed_assets.append((response.status,response.url))
                    if response.status>=400 and response.url.startswith(BASE+'/static/green/') else None)
            for width in [1440,1024,768,390]:
                page.set_viewport_size({'width':width,'height':900 if width>390 else 844})
                response=page.goto(BASE+'/landing')
                assert response.status==200
                expect(page.locator('.hero h1')).to_be_visible()
                page.evaluate('document.fonts.ready')
                assert_fit(page,'landing',width)
                for selector in ['.hero-copy','.hero-product']:
                    rect=page.locator(selector).bounding_box()
                    assert rect and rect['x']>=-1 and rect['x']+rect['width']<=width+1,(name,width,selector,rect)
                page.locator('#score').scroll_into_view_if_needed()
                expect(page.locator('[data-score-stage]')).to_be_visible()
                assert page.locator('[data-score-stage]').evaluate('(el)=>new DOMMatrixReadOnly(getComputedStyle(el).transform).isIdentity')
            login(page,'green_alpha','isolated-password-alpha')
            page.goto(BASE+'/leads')
            expect(page.locator('[data-lead-row]:visible').first).to_be_visible()
            detail=page.locator('[data-lead-row]:visible .live-company').first.get_attribute('href')
            for width in [1440,1024,768,390]:
                page.set_viewport_size({'width':width,'height':900 if width>390 else 844})
                for route in ['/', '/leads',detail,'/reports?period=monthly','/shop','/pedir','/quadro']:
                    response=page.goto(BASE+route)
                    assert response.status==200,(name,route,response.status)
                    expect(page.locator('h1')).to_be_visible()
                    assert_fit(page,route,width)
                page.goto(BASE+'/leads')
                if width<=768:
                    opener=page.get_by_role('button',name='Abrir menu',exact=True)
                    opener.click()
                    expect(page.locator('#sidebar')).to_have_class(re.compile('is-open'))
                    page.keyboard.press('Escape')
                    expect(opener).to_be_focused()
            page.goto(BASE+detail)
            draft=page.locator('[data-outreach-text]')
            draft.fill('Texto revisado & teste de compatibilidade.')
            assert parse_qs(urlsplit(page.locator('[data-message-whatsapp]').get_attribute('href')).query)['text']==[draft.input_value()]
            page.locator('[data-outreach-channel]').select_option('email')
            expect(page.locator('#action-confirm')).to_be_visible()
            page.locator('#action-confirm button[value="cancel"]').click()
            expect(page.locator('[data-outreach-channel]')).to_have_value('whatsapp')
            page.goto(BASE+'/shop')
            page.get_by_role('button',name='Trovoedas avulsas',exact=True).click()
            expect(page.locator('[data-panel="coins"]')).to_be_visible()
            expect(page.locator('[data-panel="plans"]')).to_be_hidden()
            page.goto(BASE+'/reports?period=daily')
            page.get_by_role('button',name='30 dias',exact=True).click()
            expect(page).to_have_url(BASE+'/reports?period=monthly')
            with page.expect_download() as downloaded:
                page.get_by_role('link',name=re.compile('Exportar CSV do período')).click()
            assert downloaded.value.suggested_filename=='leads_raio.csv'
            page.goto(BASE+'/logout')
            login(page,'patrao','test-only-admin-password-9284')
            for route in ['/users','/audit','/admin/leads','/admin/pedidos','/admin/ocorrencias','/bot']:
                response=page.goto(BASE+route)
                assert response.status==200,(name,route,response.status)
                assert_fit(page,route,390)
            page.goto(BASE+'/audit')
            filters=page.locator('[data-audit-filters]')
            filters.locator('[name="since"]').fill('2026-11-10')
            filters.locator('[name="until"]').fill('2000-01-01')
            filters.locator('[type="submit"]').click()
            expect(page.get_by_role('alert')).to_contain_text('Revise as datas')
            assert not errors,(name,errors)
            assert not failed_assets,(name,failed_assets)
            print(json.dumps({'engine':name,'version':browser.version,'widths':[1440,1024,768,390],
                'result':'PASS','coverage':'hero bounds/reduced motion; client navigation/overflow; mobile menu focus; editable outreach/dialog; store tabs; report CSV; admin pages/audit validation; JS/assets',
                'scope':'synthetic accounts; no persistent mutations or external sends'},ensure_ascii=False),flush=True)
            context.close()
            browser.close()


if __name__=='__main__':
    main()

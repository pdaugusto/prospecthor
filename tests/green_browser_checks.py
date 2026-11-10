"""Functional Chromium checks against the disposable server on port 8792 only.

Start green_e2e_postgres.py with GREEN_E2E_SERVE=1 GREEN_E2E_PORT=8792.
Uses synthetic accounts; never points at the integrated real-data server.
"""
import shutil
import re
import csv
import io
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright, expect

BASE = "http://127.0.0.1:8792"


def login(page, username, password):
    page.goto(BASE + "/login")
    page.locator('[name="username"]').fill(username)
    page.locator('[name="password"]').fill(password)
    page.locator('button[type="submit"]').click()
    page.wait_for_url(BASE + "/")


def main():
    with sync_playwright() as engine:
        browser = engine.chromium.launch(headless=True, executable_path=shutil.which("chromium"))
        context = browser.new_context(viewport={"width":1440,"height":1000},permissions=["clipboard-read","clipboard-write"])
        page = context.new_page()
        errors=[]
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page,"patrao","test-only-admin-password-9284")
        page.goto(BASE + "/admin/leads")
        page.locator('[data-lead-filter="owner"]').select_option("free")
        # One remaining pool record plus 25 navigation fixtures.
        rows=page.locator('[data-lead-row]:visible')
        expect(rows).to_have_count(20)
        first_page=rows.locator('.live-company strong').all_text_contents()
        page.locator('[data-page-step="1"]').click()
        expect(rows).to_have_count(6)
        second_page=rows.locator('.live-company strong').all_text_contents()
        assert not set(first_page).intersection(second_page)
        page.locator('[data-page-step="-1"]').click()
        assert rows.locator('.live-company strong').all_text_contents()==first_page
        page.locator('[data-lead-search]').fill("Navegação")
        page.locator('[data-lead-sort="name"]').click()
        expect(rows.first.locator('.live-company strong')).to_have_text("Navegação 01")
        page.locator('[data-lead-sort="name"]').click()
        expect(rows.first.locator('.live-company strong')).to_have_text("Navegação 25")
        page.locator('[data-lead-search]').fill("Navegação 25")
        expect(rows).to_have_count(1)
        expect(page.locator('[data-page-label]')).to_have_text("Página 1 de 1")
        page.locator('[data-lead-search]').fill("Navegação")
        page.locator('[data-select-visible]').check()
        expect(page.locator('[data-selected-count]')).to_have_text("20 selecionados")
        page.locator('[data-page-step="1"]').click()
        expect(rows).to_have_count(5)
        expect(page.locator('[data-select-visible]')).not_to_be_checked()
        page.get_by_text("Distribuir leads do pool",exact=True).click()
        page.locator('[data-clear-selection]').click()
        expect(page.locator('[data-selected-count]')).to_have_text("0 selecionados")
        selected=rows.first.locator('[data-select-lead]')
        selected_id=selected.get_attribute('data-select-lead')
        selected.check()
        form=page.locator('[data-live-form="distribute-selected"]')
        form.locator('[name="user_id"]').select_option(label="Alpha teste")
        form.locator('[type="submit"]').click()
        expect(page.locator('#action-confirm')).to_be_visible()
        page.locator('#action-confirm button[value="confirm"]').click()
        expect(form.locator('.form-message')).to_contain_text("Enviados 1")
        form.locator('[data-distribution-refresh]').click()
        expect(page.locator(f'[data-select-lead="{selected_id}"]')).to_be_disabled()
        # Cancelling an irreversible action does not send the deletion.
        page.locator('[data-lead-filter="owner"]').select_option("free")
        chosen=rows.first
        delete=chosen.locator('[data-api$="/delete"]')
        delete_endpoint=delete.get_attribute('data-api')
        delete.click()
        page.locator('#action-confirm button[value="cancel"]').click()
        expect(page.locator(f'[data-api="{delete_endpoint}"]')).to_be_visible()
        delete.click()
        with page.expect_navigation():
            page.locator('#action-confirm button[value="confirm"]').click()
        expect(page.locator(f'[data-api="{delete_endpoint}"]')).to_have_count(0)
        page.goto(BASE + "/audit")
        audit=page.locator('[data-audit-filters]')
        audit.locator('[name="action"]').fill('support_request')
        audit.locator('[name="username"]').fill('green_alpha')
        audit.locator('[type="submit"]').click()
        audit_rows=page.locator('.suite-table tbody tr')
        assert audit_rows.count()>0
        assert all('support_request' in text and 'green_alpha' in text for text in audit_rows.all_text_contents())
        audit.locator('[name="since"]').fill('2026-11-10')
        audit.locator('[name="until"]').fill('2000-01-01')
        audit.locator('[type="submit"]').click()
        expect(page.get_by_role('alert')).to_contain_text('Revise as datas')
        page.get_by_role('link',name='Limpar filtros',exact=True).click()
        expect(page.get_by_role('heading',name='Auditoria',exact=True)).to_be_visible()
        page.goto(BASE + "/logout")
        login(page,"green_alpha","isolated-password-alpha")
        page.goto(BASE + "/leads")
        detail=page.locator('[data-lead-row]:visible .live-company').first.get_attribute('href')
        page.goto(BASE + detail)
        draft=page.locator('[data-outreach-text]')
        edited='Mensagem personalizada & revisada para este contato.'
        draft.fill(edited)
        link=page.locator('[data-message-whatsapp]').get_attribute('href')
        assert urlsplit(link).hostname=='wa.me'
        assert parse_qs(urlsplit(link).query)['text']==[edited]
        page.locator('[data-outreach-channel]').select_option('email')
        page.locator('#action-confirm button[value="cancel"]').click()
        expect(draft).to_have_value(edited)
        expect(page.locator('[data-outreach-channel]')).to_have_value('whatsapp')
        page.locator('[data-copy-text="lead-draft"]').click()
        expect(page.locator('[data-outreach] [data-copy-status]')).to_have_text('Texto copiado.')
        assert page.evaluate('navigator.clipboard.readText()')==edited
        page.get_by_text('Resumo para copiar',exact=True).click()
        page.locator('[data-copy-text="lead-summary"]').click()
        assert page.evaluate('navigator.clipboard.readText()')==page.locator('#lead-summary').input_value()
        page.evaluate('() => { navigator.clipboard.writeText = async () => { throw new Error("isolated denial") }; }')
        page.locator('[data-copy-text="lead-draft"]').click()
        expect(page.locator('[data-outreach] [data-copy-status]')).to_contain_text('cópia automática não está disponível')
        assert draft.evaluate('(element)=>element.selectionEnd-element.selectionStart')==len(edited)
        page.locator('[data-lead-status]').select_option("convertido")
        expect(page.locator('[data-live-toast]')).to_contain_text("Etapa atualizada")
        page.reload()
        expect(page.locator('[data-lead-status]')).to_have_value("convertido")
        notes=page.locator('[data-live-form="notes"]')
        notes.locator('[name="notes"]').fill("Nota persistida pelo navegador isolado")
        notes.locator('[type="submit"]').click()
        expect(notes.locator('.form-message')).to_have_text("Anotação salva.")
        page.reload()
        expect(page.locator('[name="notes"]')).to_have_value("Nota persistida pelo navegador isolado")
        favorite=page.locator('button[data-favorite]')
        desired='false' if favorite.get_attribute('aria-pressed')=='true' else 'true'
        favorite.click()
        expect(favorite).to_have_attribute('aria-pressed',desired)
        page.reload()
        expect(page.locator('button[data-favorite]')).to_have_attribute('aria-pressed',desired)
        page.goto(BASE + "/ajuda")
        support=page.locator('[data-live-form="support"]')
        support.locator('[name="message"]').fill("Solicitação criada no navegador de teste isolado")
        support.locator('[type="submit"]').click()
        expect(support.locator('.form-message')).to_contain_text("registrada para a operação")
        page.goto(BASE + '/reports?period=daily')
        daily_total=int(re.search(r'(\d+) leads recebidos',page.locator('.live-report-period + .field-help').inner_text()).group(1))
        page.get_by_role('button',name='30 dias',exact=True).click()
        monthly_total=int(re.search(r'(\d+) leads recebidos',page.locator('.live-report-period + .field-help').inner_text()).group(1))
        assert monthly_total==daily_total+1
        chart=page.get_by_role('img',name=re.compile('Entradas diárias:'))
        assert len(chart.get_attribute('aria-label').split(':',1)[1].split(','))==30
        with page.expect_download() as download_info:
            page.get_by_role('link',name=re.compile('Exportar CSV do período')).click()
        download=download_info.value
        assert download.suggested_filename=='leads_raio.csv'
        content=__import__('pathlib').Path(download.path()).read_text(encoding='utf-8-sig')
        table=list(csv.reader(io.StringIO(content),delimiter=';'))
        assert 'Histórico Alpha' in content and 'Empresa Beta' not in content
        assert 'AssignedTo' not in table[0]
        mobile=browser.new_context(viewport={"width":390,"height":844},storage_state=context.storage_state())
        phone=mobile.new_page()
        phone.goto(BASE + "/leads")
        assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
        phone.get_by_role('button',name="Abrir menu",exact=True).click()
        expect(phone.locator('#sidebar')).to_have_class(__import__('re').compile('is-open'))
        phone.keyboard.press('Escape')
        expect(phone.get_by_role('button',name="Abrir menu",exact=True)).to_be_focused()
        assert not errors,errors
        browser.close()
    print("PASS: real Chromium UI; login, filters, pagination/sorting/selection, distribution, deletion cancel/confirm, audit filters/errors, editable outreach/WhatsApp URL, clipboard/denied clipboard, persisted status/notes/favorites, report periods/chart, CSV download, support and mobile menu/overflow. Disposable records only.")


if __name__ == '__main__':
    main()

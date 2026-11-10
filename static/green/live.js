// Real same-origin application actions. No fixtures or preview storage.
(() => {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const toast = (message, error = false) => {
    const box = $('[data-live-toast]');
    box.textContent = message; box.hidden = false;
    box.classList.toggle('is-error', error);
    clearTimeout(toast.timer); toast.timer = setTimeout(() => { box.hidden = true; }, 7000);
  };
  async function api(url, payload = {}, method = 'POST') {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(url, {
        method, credentials: 'same-origin', signal: controller.signal,
        headers: {'Content-Type': 'application/json'}, body: method === 'GET' ? undefined : JSON.stringify(payload)
      });
      if (response.status === 401) { location.assign('/login'); throw Error('Sua sessão expirou. Entre novamente.'); }
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false || data.success === false) throw Error(data.error || data.message || 'Não foi possível concluir a operação.');
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw Error('O servidor demorou a responder. Confira o estado atualizado antes de tentar novamente.');
      throw error;
    } finally { clearTimeout(timeout); }
  }
  const dialog = $('#action-confirm');
  async function confirmAction(message) {
    if (!message) return true;
    if (dialog.open) return false;
    dialog.querySelector('[data-confirm-message]').textContent = message;
    dialog.returnValue = 'cancel'; dialog.showModal();
    return new Promise(resolve => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once:true}));
  }
  async function perform(control, action) {
    if (control.dataset.busy) return;
    control.dataset.busy = 'true'; control.disabled = true;
    try { await action(); }
    catch (error) { toast(error.message, true); }
    finally { delete control.dataset.busy; control.disabled = false; }
  }
  // Sidebar keeps background inert while the mobile navigation is open.
  const side = $('.sidebar'), work = $('.workspace'), open = $('[data-open-menu]'), close = $('[data-close-menu]');
  function menu(active) {
    document.body.classList.toggle('menu-is-open', active);
    document.body.classList.toggle('sidebar-open', active);
    side?.classList.toggle('is-open', active);
    $('.sidebar-backdrop')?.classList.toggle('is-open', active);
    open?.setAttribute('aria-expanded', String(active));
    if (work) work.inert = active;
    if (side) side.inert = !active && matchMedia('(max-width:900px)').matches;
    if (active) close?.focus(); else if (document.activeElement?.closest('.sidebar') && matchMedia('(max-width:900px)').matches) open?.focus();
  }
  open?.addEventListener('click', () => menu(true));
  $$('[data-close-menu]').forEach(button => button.addEventListener('click', () => menu(false)));
  const mobile = matchMedia('(max-width:900px)'); mobile.addEventListener('change', () => menu(false)); menu(false);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') menu(false);
    if (event.key !== 'Tab' || !document.body.classList.contains('menu-is-open')) return;
    const targets = [...side.querySelectorAll('a[href],button:not(:disabled)')].filter(el => el.getClientRects().length);
    const first = targets[0], last = targets.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  $$('[data-api]').forEach(button => button.addEventListener('click', () => perform(button, async () => {
    if (!await confirmAction(button.dataset.confirm)) return;
    await api(button.dataset.api, JSON.parse(button.dataset.payload || '{}'));
    location.assign(button.dataset.reload || location.href);
  })));
  $$('[data-favorite]').forEach(button => button.addEventListener('click', () => perform(button, async () => {
    const data = await api('/api/leads/' + button.dataset.favorite + '/favorite', {fav:button.getAttribute('aria-pressed') !== 'true'});
    button.setAttribute('aria-pressed', String(data.is_favorite));
    const row = button.closest('[data-lead-row]');
    if (row) row.dataset.favorite = String(data.is_favorite);
    if (document.body.dataset.page === 'saved' && !data.is_favorite) location.reload();
    toast(data.is_favorite ? 'Lead salvo na sua conta.' : 'Lead removido dos salvos.');
  })));
  $$('[data-lead-status]').forEach(select => {
    let previous = select.value;
    select.addEventListener('change', () => perform(select, async () => {
      try {
        await api('/api/leads/' + select.dataset.leadStatus + '/status', {status:select.value});
        previous = select.value;
        if (document.body.dataset.page === 'board') location.reload();
        else toast('Etapa atualizada.');
      } catch (error) { select.value = previous; throw error; }
    }));
  });
  $$('[data-assign]').forEach(select => {
    let previous = select.value;
    select.addEventListener('change', () => perform(select, async () => {
      if (!await confirmAction('Alterar o responsável por este lead?')) { select.value = previous; return; }
      try { await api('/api/leads/' + select.dataset.assign + '/assign', {assigned_to:select.value ? Number(select.value) : null}); previous = select.value; toast('Responsável atualizado.'); }
      catch (error) { select.value = previous; throw error; }
    }));
  });
  $$('[data-user-plan]').forEach(select => {
    let previous = select.value;
    select.addEventListener('change', () => perform(select, async () => {
      const confirmation = select.value ? 'Atribuir o plano selecionado a este cliente?' : 'Remover o plano e zerar a cota de distribuição automática? O saldo e os leads já atribuídos serão mantidos.';
      if (!await confirmAction(confirmation)) { select.value = previous; return; }
      try { await api('/api/users/' + select.dataset.userPlan + '/assign-plan', {plan_slug:select.value || null}); previous = select.value; location.reload(); }
      catch (error) { select.value = previous; throw error; }
    }));
  });
  const tabs = $$('[data-tab]');
  function tabSelect(key) {
    tabs.forEach(tab => { const active = tab.dataset.tab === key; tab.classList.toggle('is-active', active); tab.setAttribute('aria-pressed', String(active)); });
    $$('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== key; });
  }
  tabs.forEach(tab => tab.addEventListener('click', () => tabSelect(tab.dataset.tab)));
  if (location.hash === '#avulsas') tabSelect('coins');
  addEventListener('hashchange', () => { if (location.hash === '#avulsas') tabSelect('coins'); });
  const leadRows = $$('[data-lead-row]');
  let leadPage = 1, leadSort = '', leadDirection = 1;
  function updateSelection() {
    const count = $$('[data-select-lead]:checked').length;
    if ($('[data-selected-count]')) $('[data-selected-count]').textContent = count + ' selecionados';
    const visible = $$('[data-lead-row]').filter(row => !row.hidden).flatMap(row => [...row.querySelectorAll('[data-select-lead]:not(:disabled)')]);
    const all = $('[data-select-visible]');
    if (all) {
      all.disabled = !visible.length;
      all.checked = visible.length > 0 && visible.every(input => input.checked);
      all.indeterminate = visible.some(input => input.checked) && !all.checked;
    }
  }
  function filterLeads() {
    if (!$('[data-lead-list]')) return;
    const query = ($('[data-lead-search]')?.value || '').toLocaleLowerCase('pt-BR').trim();
    const rows = leadRows;
    const matches = rows.filter(row => {
      const searchable = [row.dataset.name,row.dataset.city,row.dataset.niche,row.dataset.contact].join(' ').toLocaleLowerCase('pt-BR');
      const pass = (!query || searchable.includes(query)) &&
        $$('[data-lead-filter]').every(select => !select.value || row.dataset[select.dataset.leadFilter] === select.value) &&
        (!$('[data-only-hot]')?.checked || Number(row.dataset.score) >= 80);
      row.hidden = true;
      return pass;
    });
    if (leadSort) matches.sort((a,b) => leadDirection * (leadSort === 'score'
      ? Number(a.dataset.score) - Number(b.dataset.score)
      : String(a.dataset[leadSort] || '').localeCompare(String(b.dataset[leadSort] || ''),'pt-BR',{numeric:true,sensitivity:'base'})));
    const size = Number($('[data-page-size]')?.value) || 20;
    const pages = Math.max(1, Math.ceil(matches.length / size));
    leadPage = Math.min(pages, Math.max(1, leadPage));
    const visible = matches.slice((leadPage - 1) * size, leadPage * size);
    visible.forEach(row => { row.parentNode.append(row); row.hidden = false; });
    if ($('[data-list-count]')) $('[data-list-count]').textContent = matches.length + ' leads encontrados · ' + visible.length + ' nesta página';
    if ($('[data-list-empty]')) $('[data-list-empty]').hidden = matches.length > 0 || !rows.length;
    if ($('[data-page-label]')) $('[data-page-label]').textContent = 'Página ' + leadPage + ' de ' + pages;
    $$('[data-page-step]').forEach(button => { button.disabled = Number(button.dataset.pageStep) < 0 ? leadPage === 1 : leadPage === pages; });
    updateSelection();
  }
  $$('[data-lead-search],[data-lead-filter],[data-only-hot],[data-page-size]').forEach(input => input.addEventListener('input', () => { leadPage = 1; filterLeads(); }));
  $$('[data-page-step]').forEach(button => button.addEventListener('click', () => { leadPage += Number(button.dataset.pageStep); filterLeads(); }));
  $$('[data-lead-sort]').forEach(button => button.addEventListener('click', () => {
    leadDirection = leadSort === button.dataset.leadSort ? -leadDirection : (button.dataset.leadSort === 'score' ? -1 : 1);
    leadSort = button.dataset.leadSort; leadPage = 1;
    $$('[data-lead-sort]').forEach(control => control.closest('th').setAttribute('aria-sort',control === button ? (leadDirection === 1 ? 'ascending' : 'descending') : 'none'));
    filterLeads();
  }));
  $$('[data-select-lead]').forEach(input => input.addEventListener('change', updateSelection));
  $('[data-select-visible]')?.addEventListener('change', event => {
    $$('[data-select-lead]:not(:disabled)').filter(input => !input.closest('[data-lead-row]').hidden).forEach(input => { input.checked = event.target.checked; }); updateSelection();
  });
  $('[data-clear-selection]')?.addEventListener('click', () => { $$('[data-select-lead]').forEach(input => { input.checked = false; }); updateSelection(); });
  filterLeads();
  const outreach = $('[data-outreach]');
  if (outreach) {
    const channel = outreach.querySelector('[data-outreach-channel]');
    const text = outreach.querySelector('[data-outreach-text]');
    const drafts = JSON.parse(outreach.dataset.drafts);
    let previous = channel.value;
    const updateMessageLink = () => {
      const link = outreach.querySelector('[data-message-whatsapp]');
      if (!link) return;
      const url = new URL(link.dataset.baseUrl);
      if (text.value.trim()) url.searchParams.set('text', text.value.trim());
      link.href = url.href; link.hidden = !text.value.trim();
    };
    text.addEventListener('input', updateMessageLink);
    channel.addEventListener('change', async () => {
      if (text.value !== drafts[previous] && !await confirmAction('Substituir sua edição pelo rascunho do canal selecionado?')) { channel.value = previous; return; }
      text.value = drafts[channel.value]; previous = channel.value; updateMessageLink();
    });
    updateMessageLink();
  }
  $$('[data-copy-text]').forEach(button => button.addEventListener('click', () => perform(button, async () => {
    const text = document.getElementById(button.dataset.copyText);
    const status = button.closest('.suite-form').querySelector('[data-copy-status]');
    try {
      if (!navigator.clipboard?.writeText) throw Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text.value);
      status.classList.remove('is-error'); status.textContent = 'Texto copiado.';
    } catch {
      text.focus(); text.select();
      status.classList.add('is-error'); status.textContent = 'A cópia automática não está disponível. O texto está selecionado: copie pelo menu do navegador ou pelo teclado.';
    }
  })));
  const orderForm = $('[data-live-form="order"]');
  function orderSummary() {
    if (!orderForm) return;
    const quantity = Math.max(0, Number(orderForm.elements.quantity.value) || 0);
    const random = quantity < 10;
    $('[data-order-total]').textContent = quantity;
    $('[data-order-random]').hidden = !random; $('[data-order-segment]').hidden = random;
    ['city','niche'].forEach(key => { orderForm.elements[key].disabled = random; });
  }
  orderForm?.elements.quantity.addEventListener('input', orderSummary); orderSummary();
  $$('[data-live-form]').forEach(form => form.addEventListener('submit', event => {
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    const message = form.querySelector('.form-message');
    perform(button, async () => {
      message.textContent = ''; message.classList.remove('is-error');
      const data = Object.fromEntries(new FormData(form));
      try {
        switch (form.dataset.liveForm) {
          case 'distribute-selected': {
            const ids = $$('[data-select-lead]:checked:not(:disabled)').map(input => Number(input.dataset.selectLead));
            if (!ids.length) throw Error('Selecione pelo menos um lead livre na tabela.');
            if (!await confirmAction('Distribuir ' + ids.length + ' leads selecionados para ' + form.elements.user_id.selectedOptions[0].textContent + '? Esta ação altera o responsável, sem reserva de Trovoedas.')) return;
            const result = await api('/api/leads/distribute',{lead_ids:ids,user_id:Number(data.user_id),respect_quota:form.elements.respect_quota.checked});
            message.textContent = result.message || (result.distributed + ' leads distribuídos.');
            if (Number(result.distributed) > 0) { button.hidden = true; form.querySelector('[data-distribution-refresh]').hidden = false; }
            break;
          }
          case 'distribute-auto': {
            if (!await confirmAction('Distribuir automaticamente até ' + data.limit + ' leads livres, respeitando as cotas?')) return;
            const result = await api('/api/leads/distribute',{limit:Number(data.limit),user_id:data.user_id ? Number(data.user_id) : null});
            message.textContent = result.message || (result.distributed + ' leads distribuídos.');
            if (Number(result.distributed) > 0) { button.hidden = true; form.querySelector('[data-distribution-refresh]').hidden = false; }
            break;
          }
          case 'user-create':
            if (!await confirmAction('Criar o acesso deste cliente com a cota informada?')) return;
            await api('/api/users', {...data, monthly_quota:Number(data.monthly_quota), role:'client'});
            form.reset(); location.reload(); break;
          case 'user-edit':
            if (!data.password) delete data.password;
            if (!await confirmAction('Salvar as alterações de acesso e cota deste cliente?')) return;
            await api('/api/users/' + form.dataset.userId, {...data, monthly_quota:Number(data.monthly_quota)});
            location.reload(); break;
          case 'notes': await api('/api/leads/' + form.dataset.id + '/status', {notes:data.notes}); message.textContent = 'Anotação salva.'; break;
          case 'report':
            if (!await confirmAction('Enviar este contato para análise de reposição?')) return;
            await api('/api/leads/' + form.dataset.id + '/report', data); message.textContent = 'Solicitação de análise registrada.'; button.hidden = true; break;
          case 'support':
            const ticket = await api('/api/support', data); message.textContent = 'Solicitação #' + ticket.id + ' registrada para a operação.'; form.reset(); break;
          case 'grant':
            if (!Number.isInteger(Number(data.amount)) || Number(data.amount) === 0) throw Error('Informe uma quantidade inteira diferente de zero.');
            if (!await confirmAction('Ajustar o saldo deste cliente em ' + data.amount + ' Trovoedas?')) return;
            await api('/api/trovoeda/grant', {...data,user_id:Number(form.dataset.userId),amount:Number(data.amount)}); location.reload(); break;
          case 'order':
            if (!await confirmAction('Reservar ' + data.quantity + ' Trovoedas e enviar o pedido de leads?')) return;
            const order = await api('/api/orders', {...data,quantity:Number(data.quantity)});
            if (!order.order?.id) throw Error('Pedido enviado, mas o servidor não retornou o código. Consulte Meus pedidos antes de repetir.');
            location.assign('/pedido/' + order.order.id); break;
          case 'checkout':
            const checkout = await api('/api/trovoeda/checkout', {package:data.package});
            const target = new URL(checkout.url);
            if (target.protocol !== 'https:' || target.hostname !== 'checkout.stripe.com') throw Error('O servidor retornou um endereço de pagamento inesperado.');
            location.assign(target.href); break;
        }
      } catch (error) { message.textContent = error.message; message.classList.add('is-error'); }
    });
  }));
})();

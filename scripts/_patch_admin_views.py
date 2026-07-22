# -*- coding: utf-8 -*-
"""Replace users/audit/bot/reports views with professional SaaS UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
index = ROOT / "templates" / "index.html"
text = index.read_text(encoding="utf-8")

START = '        <section id="view-users" class="view">'
END = '    </main>'

i = text.find(START)
j = text.find(END)
if i < 0 or j < 0 or j <= i:
    raise SystemExit(f"markers not found i={i} j={j}")

NEW = r'''        <section id="view-users" class="view">
            <div class="admin-page">
                <div class="admin-hero">
                    <div>
                        <div class="admin-kicker">Admin · Host</div>
                        <h2>Clientes &amp; Trovoedas</h2>
                        <p>Gerencie contas, saldos e quem recebe leads. Sua home mostra sobras; aqui você controla o time.</p>
                    </div>
                </div>

                <div class="admin-grid-2">
                    <div class="admin-card">
                        <h3>Novo cliente</h3>
                        <p class="admin-card-sub">Cria login SaaS com cota opcional (legado bot) e saldo em Trovoedas via bônus depois.</p>
                        <div class="admin-form-row">
                            <input type="text" id="new-user-name" placeholder="Login" autocomplete="off">
                            <input type="password" id="new-user-pass" placeholder="Senha">
                            <input type="text" id="new-user-label" placeholder="Nome exibido">
                            <input type="number" id="new-user-quota" placeholder="Cota/mês" value="0" title="0 = só Trovoedas / pedidos">
                            <button class="btn btn-primary" type="button" onclick="createUser()">Criar</button>
                        </div>
                    </div>
                    <div class="admin-card admin-tips">
                        <h3>Atalhos</h3>
                        <ul>
                            <li><strong>Cota 0</strong> — não entra na distribuição automática do bot</li>
                            <li><strong>+Trovoedas</strong> — credita moedas SaaS (1 = 1 lead)</li>
                            <li><strong>Remover</strong> — conta some; leads voltam pro pool</li>
                            <li><strong>Impersonate</strong> — vê o painel como o cliente</li>
                        </ul>
                    </div>
                </div>

                <div class="admin-card" style="padding:0;overflow:hidden;">
                    <div class="admin-table-head">
                        <h3>Equipe</h3>
                        <button class="btn btn-sm" type="button" onclick="loadUsers()">Atualizar</button>
                    </div>
                    <div class="table-wrap" style="border:none;border-radius:0;">
                        <table class="admin-table">
                            <thead>
                                <tr>
                                    <th>Recebe leads</th>
                                    <th>Login</th>
                                    <th>Nome</th>
                                    <th>Papel</th>
                                    <th>Cota/mês</th>
                                    <th><img class="coin-mini" src="/static/trovoeda-coin.png?v=15" alt="" /> Trovoedas</th>
                                    <th>Recebidos</th>
                                    <th style="min-width:280px;">Ações</th>
                                </tr>
                            </thead>
                            <tbody id="users-tbody"></tbody>
                        </table>
                    </div>
                    <p id="users-hint" class="admin-hint"></p>
                </div>
            </div>
        </section>

        <section id="view-audit" class="view">
            <div class="admin-page">
                <div class="admin-hero">
                    <div>
                        <div class="admin-kicker">Admin · Host</div>
                        <h2>Auditoria</h2>
                        <p>Histórico de ações no sistema — quem fez o quê, em qual lead, e quando.</p>
                    </div>
                </div>
                <div class="admin-card">
                    <h3>Filtros</h3>
                    <div class="admin-form-row">
                        <input type="text" id="audit-user" placeholder="Usuário">
                        <input type="text" id="audit-action" placeholder="Ação (ex: order_approve)">
                        <input type="date" id="audit-since">
                        <input type="date" id="audit-until">
                        <button class="btn btn-primary" type="button" onclick="loadAudit()">Filtrar</button>
                    </div>
                </div>
                <div class="admin-card" style="padding:0;overflow:hidden;">
                    <div class="table-wrap" style="border:none;border-radius:0;">
                        <table class="admin-table">
                            <thead>
                                <tr>
                                    <th>Data/Hora</th>
                                    <th>Usuário</th>
                                    <th>Ação</th>
                                    <th>Lead / Empresa</th>
                                    <th>Detalhes</th>
                                </tr>
                            </thead>
                            <tbody id="audit-tbody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </section>

        <section id="view-bot" class="view">
            <div class="admin-page">
                <div class="admin-hero">
                    <div>
                        <div class="admin-kicker">Operação · Cockpit</div>
                        <h2>Status do robô</h2>
                        <p>Missões e planos rodam no <strong style="color:var(--text)">Cockpit local</strong>. Aqui você acompanha se está rodando, o progresso da meta e os logs — sem marcar nicho/cidade no painel.</p>
                    </div>
                    <div class="admin-hero-actions">
                        <button class="btn btn-sm" type="button" onclick="loadBotStatus()">Atualizar</button>
                        <button class="btn btn-danger btn-sm" type="button" id="btn-bot-force-stop" onclick="forceBotStop()">Marcar parado</button>
                    </div>
                </div>

                <div class="bot-status-grid" id="bot-status-grid">
                    <div class="bot-status-card live" id="bot-card-live">
                        <div class="bot-status-label">Estado agora</div>
                        <div class="bot-status-value" id="bot-status-label">—</div>
                        <div class="bot-status-meta" id="bot-status-hint">Carregando…</div>
                    </div>
                    <div class="bot-status-card">
                        <div class="bot-status-label">Progresso da meta</div>
                        <div class="bot-status-value sm" id="bot-meta-progress">—</div>
                        <div class="bot-progress"><i id="bot-meta-bar" style="width:0%"></i></div>
                        <div class="bot-status-meta" id="bot-meta-detail">Meta da missão atual</div>
                    </div>
                    <div class="bot-status-card">
                        <div class="bot-status-label">Última execução</div>
                        <div class="bot-status-value sm" id="bot-last-run">—</div>
                        <div class="bot-status-meta" id="bot-last-job">Job: —</div>
                    </div>
                    <div class="bot-status-card">
                        <div class="bot-status-label">Leads da sessão</div>
                        <div class="bot-status-value" id="bot-last-leads">—</div>
                        <div class="bot-status-meta">Contagem compartilhada Maps + Fonte B</div>
                    </div>
                </div>

                <div class="admin-grid-2">
                    <div class="admin-card">
                        <h3>O que monitorar</h3>
                        <ul class="bot-checklist">
                            <li><strong>Rodando</strong> — heartbeat atualiza; se travar &gt;20 min, auto-corrige</li>
                            <li><strong>Meta</strong> — session_leads / mission_target</li>
                            <li><strong>Job</strong> — etapa atual (Maps, score, META_OK…)</li>
                            <li><strong>Cockpit</strong> — cria e enfileira missões no PC local</li>
                        </ul>
                        <p class="admin-hint" style="margin-top:12px;">Plano de nichos/cidades: use o Cockpit. Este painel é o radar de produtividade.</p>
                    </div>
                    <div class="admin-card">
                        <h3>Resumo rápido</h3>
                        <div class="bot-kv" id="bot-kv">
                            <div><span>Iniciou</span><b id="bot-started">—</b></div>
                            <div><span>Terminou</span><b id="bot-finished">—</b></div>
                            <div><span>Heartbeat</span><b id="bot-updated">—</b></div>
                            <div><span>Stale</span><b id="bot-stale">—</b></div>
                        </div>
                        <div id="bot-error" style="color:var(--danger);font-size:13px;margin-top:12px;"></div>
                    </div>
                </div>

                <div class="admin-card">
                    <div class="admin-table-head">
                        <h3>Logs ao vivo</h3>
                        <button class="btn btn-sm" type="button" onclick="loadBotStatus()">Atualizar logs</button>
                    </div>
                    <div id="bot-logs" class="bot-logs">Carregando…</div>
                </div>
            </div>
        </section>

        <!-- Edit user modal -->
        <div class="modal-bg" id="user-edit-modal">
            <div class="modal">
                <div class="modal-head">
                    <h3>Editar usuário</h3>
                    <button class="btn btn-sm" onclick="closeUserEdit()">Fechar</button>
                </div>
                <div class="modal-body">
                    <input type="hidden" id="edit-user-id">
                    <div class="form-g">
                        <label>Login</label>
                        <input type="text" id="edit-user-username">
                    </div>
                    <div class="form-g">
                        <label>Nome exibido</label>
                        <input type="text" id="edit-user-label" placeholder="Ex: João">
                    </div>
                    <div class="form-g">
                        <label>Papel</label>
                        <select id="edit-user-role">
                            <option value="client">Cliente</option>
                            <option value="admin" disabled>Admin (só Patrão)</option>
                        </select>
                    </div>
                    <div class="form-g">
                        <label>Cota mensal (0 = só Trovoedas/pedidos)</label>
                        <input type="number" id="edit-user-quota" min="0">
                    </div>
                    <div class="form-g">
                        <label>Nova senha (vazio = não muda)</label>
                        <input type="password" id="edit-user-pass" placeholder="••••••••">
                    </div>
                    <div class="form-g">
                        <label>Recebe leads do bot</label>
                        <select id="edit-user-active">
                            <option value="1">Sim</option>
                            <option value="0">Não</option>
                        </select>
                    </div>
                    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px;">
                        <button class="btn btn-primary" onclick="saveUserEdit()">Salvar</button>
                        <button class="btn" onclick="zeroUserQuota()">Zerar cota</button>
                        <button class="btn" onclick="resetUserMonth()">Zerar uso do mês</button>
                        <button class="btn btn-danger" onclick="deleteUserFromEdit()">Remover</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- LEADS -->
        <section id="view-leads" class="view">
            <div class="leads-head-actions">
                <button type="button" class="btn btn-primary" id="btn-distribute-leads" style="display:none;" onclick="openDistributeModal()">Distribuir sobra</button>
                <a class="btn btn-secondary" id="export-csv-btn" href="/api/export/csv?scope=free">CSV</a>
            </div>
            <div class="chip-filters" id="status-chips">
                <button type="button" class="chip on" data-st="" onclick="setStatusChip('', this)">Todos</button>
                <button type="button" class="chip" data-st="novo" onclick="setStatusChip('novo', this)">Novos</button>
                <button type="button" class="chip" data-st="contactado" onclick="setStatusChip('contactado', this)">Contactados</button>
                <button type="button" class="chip" data-st="convertido" onclick="setStatusChip('convertido', this)">Convertidos</button>
                <button type="button" class="chip" data-st="descartado" onclick="setStatusChip('descartado', this)">Descartados</button>
            </div>
            <div class="filters leads-toolbar">
                <select id="filter-scope" style="display:none;" title="Escopo do Patrão">
                    <option value="free" selected>Só sobras (livres)</option>
                    <option value="all">Todos os leads</option>
                    <option value="user">Por usuário…</option>
                </select>
                <select id="filter-owner" style="display:none;" title="Usuário dono">
                    <option value="">Escolha o usuário</option>
                </select>
                <div class="search-wrap">
                    <span class="search-ico">⌕</span>
                    <input type="search" id="filter-search" placeholder="Buscar empresa, cidade, telefone…">
                </div>
                <select id="filter-niche"><option value="">Todos os nichos</option></select>
                <select id="filter-status">
                    <option value="">Todos os status</option>
                    <option value="novo">Novo</option>
                    <option value="contactado">Contactado</option>
                    <option value="convertido">Convertido</option>
                    <option value="descartado">Descartado</option>
                </select>
            </div>
            <div class="table-wrap" id="leads-table-wrap">
                <table>
                    <thead>
                        <tr>
                            <th onclick="sortBy('name')">Empresa</th>
                            <th onclick="sortBy('city')">Cidade</th>
                            <th onclick="sortBy('phone')">Telefone</th>
                            <th onclick="sortBy('lead_score')" title="Score 0–100">Score <span style="font-weight:500;color:var(--muted);font-size:10px;">/100</span></th>
                            <th onclick="sortBy('status')">Status</th>
                            <th>Ações</th>
                        </tr>
                    </thead>
                    <tbody id="leads-tbody"></tbody>
                </table>
            </div>
            <div class="mobile-list" id="leads-mobile"></div>
            <div class="pagination" id="leads-pagination"></div>
        </section>

        <section id="view-lead-detail" class="view">
            <div style="margin-bottom:14px;">
                <button class="btn" onclick="navigateTo('/leads')">← Voltar</button>
            </div>
            <div id="lead-detail-wrapper" class="detail-grid"></div>
        </section>

        <section id="view-reports" class="view">
            <div class="reports-page">
                <div class="admin-hero">
                    <div>
                        <div class="admin-kicker">Insights</div>
                        <h2>Relatórios</h2>
                        <p id="reports-sub">Performance da sua carteira de leads no período selecionado.</p>
                    </div>
                    <div class="admin-hero-actions">
                        <select id="report-period" onchange="loadReports()" class="report-period-select">
                            <option value="daily">Hoje</option>
                            <option value="weekly" selected>7 dias</option>
                            <option value="monthly">30 dias</option>
                        </select>
                    </div>
                </div>

                <div class="stats reports-stats">
                    <div class="stat neon">
                        <div class="stat-top"><h3>Leads no período</h3></div>
                        <div class="value" id="report-total">—</div>
                        <div class="stat-meta">Entraram na sua lista</div>
                    </div>
                    <div class="stat">
                        <div class="stat-top"><h3>Novos</h3></div>
                        <div class="value" id="report-novos">—</div>
                        <div class="stat-meta" id="report-rate">— do total</div>
                    </div>
                    <div class="stat warn">
                        <div class="stat-top"><h3>Contactados</h3></div>
                        <div class="value" id="report-contact">—</div>
                        <div class="stat-meta">Com follow-up registrado</div>
                    </div>
                    <div class="stat ok">
                        <div class="stat-top"><h3>Conversão</h3></div>
                        <div class="value" id="report-conv">—</div>
                        <div class="stat-meta">Sobre contactados</div>
                    </div>
                </div>

                <div class="charts">
                    <div class="panel">
                        <h3>Funil do período</h3>
                        <div class="chart-box"><canvas id="chart-report"></canvas></div>
                    </div>
                    <div class="panel">
                        <h3>Sinais / problemas</h3>
                        <div class="chart-box"><canvas id="chart-problems"></canvas></div>
                    </div>
                </div>

                <div class="admin-card" id="reports-empty-hint" style="display:none;">
                    <h3>Sem dados neste período</h3>
                    <p class="admin-card-sub">Peça leads ou aborde a carteira — os gráficos enchem conforme você usa o painel.</p>
                    <button type="button" class="btn btn-primary" onclick="navigateTo('/orders')">Pedir leads</button>
                </div>
            </div>
        </section>
'''

# Keep content after view-users until main, but we need to also keep leads modal etc that was BETWEEN reports and main
# Actually original structure was: users, audit, bot, edit modal, leads, detail, reports, </main>
# Our NEW includes all of that through reports. What about distribute modal and other modals AFTER main content?
# Check what comes after view-reports section before </main>

after = text[j:]
# verify
print("Replacing from", i, "to", j, "len old", j-i, "len new", len(NEW))
out = text[:i] + NEW + "\n" + after
index.write_text(out, encoding="utf-8")
print("OK wrote index.html")

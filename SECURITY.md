# Segurança — ProspecTHOR / Prospector Bot

Checklist para vender o produto sem expor GitHub, Supabase e Vercel.

---

## 0. Urgente (faça hoje)

1. **Troque a senha do Patrão** (a antiga pode ter aparecido em código/exemplo).
2. Na **Vercel → Project → Settings → Environment Variables**:
   - `DASHBOARD_PASS` = senha nova forte
   - `FLASK_SECRET_KEY` = `python -c "import secrets; print(secrets.token_hex(32))"`
   - `DATABASE_URL` = string do Supabase (nunca no Git)
3. **Supabase → Settings → Database**: se a connection string vazou, **reset password** do DB e atualize a Vercel.
4. Confirme que `.env` **não** está no Git (`git ls-files .env` deve ficar vazio).
5. GitHub: repo **Private**; ative 2FA na conta.

---

## 1. GitHub

| Ação | Por quê |
|------|---------|
| Repo **privado** | Código + estrutura = alvo |
| **2FA** na conta e em colaboradores | Conta roubada = push malicioso |
| **Branch protection** em `main` | Exige PR / review antes de merge |
| **Secrets** só em GitHub Actions (se usar CI) | Nunca no código |
| Não commitar `.env`, dumps, CSV de leads | LGPD + credenciais |
| Revisar **Deploy keys / PATs** antigos | Revogar o que não usa |
| Dependabot alerts (opcional) | Avisos de libs vulneráveis |

```bash
# Conferir se segredo entrou no histórico
git log --all --full-history -- .env .env.txt
# Se aparecer, troque as senhas (mesmo após remover o arquivo)
```

---

## 2. Supabase (Postgres)

| Ação | Por quê |
|------|---------|
| Senha do DB **forte** e rotacionada se vazou | `DATABASE_URL` é a chave do cofre |
| Use **connection pooling** (porta 6543) na Vercel se possível | Menos conexões abertas |
| **Não** exponha `service_role` no frontend | Essa key bypassa RLS |
| Preferir **RLS** se um dia usar Supabase client no browser | Hoje o app usa só server-side `psycopg2` — bom |
| Restrinja IPs se o plano permitir | Menos superfície |
| Backups automáticos ligados | Ransomware / erro humano |
| Não use a mesma senha do dashboard no DB | Separação de segredos |

O app acessa o banco **só no servidor** (Vercel function + bot local). Clientes **nunca** recebem a `DATABASE_URL`.

---

## 3. Vercel

| Ação | Por quê |
|------|---------|
| Env vars só em **Production/Preview** conforme necessidade | Preview não precisa de DB real se possível |
| `FLASK_SECRET_KEY` **fixo e longo** | Cookie de sessão assinado |
| `DASHBOARD_PASS` forte | Login do Patrão |
| Não logar `DATABASE_URL` / senhas | Logs da Vercel podem vazar |
| Domínio próprio + HTTPS (já vem) | Cookie Secure |
| Revisar quem tem acesso ao time Vercel | Mesma lógica do GitHub |

Gere secret:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 4. App (já reforçado no código)

- Sem **senha default** no código
- Hash de senha **pbkdf2** (migra SHA256 no login)
- Cookie de sessão: **HttpOnly**, **SameSite=Lax**, **Secure** na Vercel
- Sessão ~**12h**
- **Rate limit** de login (anti brute-force, best-effort em serverless)
- Cliente só vê **leads dele** + com telefone
- Rotas admin só **patrao**

Ainda recomendado depois:

- [ ] Trocar senhas de **todos** os clientes ao vender
- [ ] 2FA opcional / magic link (futuro)
- [ ] Auditoria de logins (IP, horário)
- [ ] WAF / Cloudflare na frente do domínio
- [ ] Política de senha mínima (8–12 chars) na criação de usuário

---

## 5. Operação comercial (multi-cliente)

| Risco | Mitigação |
|-------|-----------|
| Cliente A ver leads do B | Isolamento `assigned_to` (já) |
| Cliente virar admin | Só username `patrao` é admin (já) |
| Conta inativa logar | `active=0` bloqueia login (já) |
| Funcionário vazar dados | Auditoria + impersonate logado (já) |
| Repo público por engano | Manter private + alertas |

---

## 6. Ordem prática (1 hora)

1. Gerar `FLASK_SECRET_KEY` nova → colar na Vercel  
2. Trocar `DASHBOARD_PASS` → Vercel + `.env` local  
3. Trocar senha do Postgres no Supabase → atualizar `DATABASE_URL`  
4. Redeploy Vercel  
5. Login de teste como patrao + fafa  
6. GitHub: 2FA + repo private + branch protection  
7. Remover do Git qualquer `.env.txt` / dump (e trocar o que vazou)

---

## 7. O que **não** fazer

- Colocar senha real em README / chat / print  
- Commitar `.env`  
- Compartilhar `DATABASE_URL` com cliente  
- Deixar conta `admin` com role admin  
- Usar a mesma senha em GitHub, Supabase, Vercel e painel  

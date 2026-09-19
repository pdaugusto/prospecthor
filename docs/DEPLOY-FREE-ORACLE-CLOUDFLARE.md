# Deploy grátis: Oracle Cloud "Always Free" + Cloudflare Tunnel

> Objetivo: rodar o **cockpit 24/7 de graça na nuvem**, sem abrir porta pública,
> com acesso HTTPS seguro e protegido por e-mail (Zero Trust do Cloudflare).
> O site público (Vercel) e o banco (Supabase) NÃO mudam.

## Arquitetura

```
┌────────────────────────────────────────────────────────────────────┐
│  Você (navegador)                                                   │
│   https://cockpit.prospecthor.online  ──► Cloudflare Edge (R$ 0,00) │
└──────────────────────────────┬─────────────────────────────────────┘
                               │  Tunnel (cloudflared, outbound TCP)
                               ▼
┌────────────────────────────────────────────────────────────────────┐
│  VM Oracle Cloud "Always Free" (custo R$ 0,00)                     │
│   Ubuntu 24.04 · Ampere ARM 4 OCPU / 24 GB RAM                     │
│   ┌─────────────────────────────────────────────────┐               │
│   │  ProspecTHOR Cockpit (Flask, porta 5055)      │  ← systemd      │
│   │  Scheduler diário (main.py schedule) — opcional│  ← systemd      │
│   │  Playwright Chromium (Fonte A / Maps)         │                 │
│   └─────────────────────────────────────────────────┘               │
└────────────────────────────────────────────────────────────────────┘

Supabase (Postgres) — já na nuvem, inalterado
Vercel (prospecthor.online) — já no ar, inalterado
```

Por que funciona: o tráfego do cockpit sai **da VM** (cloudflared abre conexão
de saída para a Cloudflare). Nenhuma porta chega exposta ao mundo; o acesso
passa pela rede da Cloudflare + policy de e-mail. O `google_maps.py` e o
cockpit já são cross-platform (`python_exe()`, `sys.platform`), então o código
sobe no Ubuntu **sem alteração**.

---

## 0. Pré-requisitos

- Conta [Oracle Cloud Free Tier](https://www.oracle.com/cloud/free/) (pede cartão para verificação, mas a Always Free é gratuita para sempre).
- Conta [Cloudflare](https://dash.cloudflare.com/) grátis.
- Domínio **prospecthor.online** gerenciado pela Cloudflare (para usar `cockpit.prospecthor.online` no Tunnel).

> Se o DNS do domínio não estiver na Cloudflare: ou move o DNS para a Cloudflare
> (grátis, mantém Vercel apontando), ou gera uma URL temporária de tunnel
> (`.trycloudflare.com` — muda a cada reinício).

---

## 1. Criar a VM na Oracle Cloud

1. Console → **Compute → Instances → Create instance**.
2. **Image**: Ubuntu 24.04 (canonical), arquitetura **ARM**.
3. **Shape**: `VM.Standard.A1.Flex (Ampere)` → ajuste para **4 OCPU / 24 GB RAM**
   (tudo dentro da franquia Always Free; 24 GB deixam o Chromium folgado).
4. **Boot volume**: 50 GB (Always Free inclui até 200 GB).
5. **SSH**: gere um par de chaves localmente e envie a **pública**:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/prospecthor_oracle -C "prospecthor"   # na sua máquina (WSL)
   ```
   No campo "Add SSH keys" da Oracle, cole o conteúdo de `~/.ssh/prospecthor_oracle.pub`.

6. **Atenção**: a franquia ARM pode aparecer "out of capacity" — troque de
   *availability domain* (região) ou tente em outro horário; a reserva é liberada aos poucos.
7. Crie e anote o **IP público**.

Teste de acesso (na sua máquina/WSL):

```bash
ssh -i ~/.ssh/prospecthor_oracle ubuntu@<IP_PUBLICO>
```

---

## 2. Preparar o sistema (dentro da VM)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git python3-venv python3-pip ca-certificates curl gpg

# Fuso horário certo para o scheduler (08h/20h = hora de Brasília)
sudo timedatectl set-timezone America/Sao_Paulo
timedatectl
```

---

## 3. Instalar o Projeto + Playwright

```bash
cd ~
git clone https://github.com/pdaugusto/prospecthor.git
cd prospecthor

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Chromium + dependências de sistema do Linux (o --with-deps usa apt via sudo)
playwright install --with-deps chromium
```

Aviso: `venv/Scripts/python.exe` não existe no Linux — o `python_exe()` do
cockpit faz fallback para `sys.executable`, então use **sempre** o python do venv.

---

## 4. Copiar `.env` e `data/` da sua máquina

Na sua máquina (WSL), a partir do projeto:

```bash
cd /mnt/c/Users/Chefão/Downloads/Prospecthor/Prospecthor

scp -i ~/.ssh/prospecthor_oracle .env         ubuntu@<IP_PUBLICO>:~/prospecthor/.env
scp -i ~/.ssh/prospecthor_oracle -r data      ubuntu@<IP_PUBLICO>:~/prospecthor/data
```

Verificações do arquivo `.env` antes de subir:
- `DATABASE_URL` apontando para o Supabase (os leads **ficam lá** — seguros).
- `DASHBOARD_USER` / `DASHBOARD_PASS` = credenciais fortes (o Tunnel vai expor a tela de login).
- `FLASK_SECRET_KEY` / `DASHBOARD_SECRET_KEY` = valores aleatórios.
- `.env` NUNCA vai para o git (está no `.gitignore`).

## 5. Teste manual antes de virar serviço

```bash
cd ~/prospecthor
source venv/bin/activate
export PYTHONUTF8=1
python main.py status          # confere banco/conexões
python -c "from cockpit.app import app; app.run(host='127.0.0.1', port=5055)"   # Ctrl+C após ver "Running on"
```

---

## 6. Sistema `systemd` para o Cockpit (porta 5055)

Crie `/etc/systemd/system/prospecthor-cockpit.service`:

```ini
[Unit]
Description=ProspecTHOR Cockpit (Flask 5055)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/prospecthor
Environment=PYTHONUTF8=1
ExecStart=/home/ubuntu/prospecthor/venv/bin/python -c "from cockpit.app import app; app.run(host='127.0.0.1', port=5055, debug=False, threaded=True)"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> `cockpit/app.py` já faz `load_dotenv(ROOT/'.env')` — não precisa de EnvironmentFile.
> O `start.py` só abre navegador (sem utilidade em servidor); por isso rodamos via `-c`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now prospecthor-cockpit
sudo systemctl status prospecthor-cockpit
curl -s http://127.0.0.1:5055/ | head -c 200
```

---

## 7. (Opcional) Agendador diário como serviço

Se quiser as buscas automáticas dos horários (08/10/14/16/20/22) além das missões manuais:

Crie `/etc/systemd/system/prospecthor-scheduler.service`:

```ini
[Unit]
Description=ProspecTHOR Scheduler diario
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/prospecthor
Environment=PYTHONUTF8=1
ExecStart=/home/ubuntu/prospecthor/venv/bin/python /home/ubuntu/prospecthor/main.py schedule
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now prospecthor-scheduler
```

---

## 8. Cloudflare Tunnel (acesso grátis e seguro)

1. Cloudflare dashboard → **Zero Trust → Networks → Tunnels → Create a tunnel** →
   escolha **Cloudflared** → dê um nome (ex: `prospecthor`) → copie o **token**.
2. Instale o `cloudflared` na VM:

   ```bash
   curl -L https://pkg.cloudflare.com/cloudflare-main.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
   echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflare.com/cloudflared any main" | sudo tee /etc/apt/sources.list.d/cloudflared.list
   sudo apt update && sudo apt install -y cloudflared
   ```

3. Registre como serviço com o token (roda em background e persiste reboot):

   ```bash
   sudo cloudflared service install <TOKEN_DO_TUNNEL>
   sudo systemctl status cloudflared
   ```

4. No dashboard do tunnel → **Public Hostnames → Add**:
   - Subdomain: `cockpit` · Domain: `prospecthor.online`
   - Service: `http://localhost:5055`
   (o tráfego HTTPS é provisionado automaticamente pela Cloudflare)

5. Proteja com e-mail (Zero Trust **free**, até 50 usuários):
   **Zero Trust → Access → Applications → Add an application** (tipo *Self-hosted*):
   - Domínio: `cockpit.prospecthor.online`
   - Policy: **Allow** o **seu e-mail** (ou inclua a equipe), com `Session duration` 1 dia.

Agora **nenhuma porta fica aberta** na VM (nem 5055, nem SSH exposto por regra de firewall da Oracle — só libere SSH para o seu IP, se quiser).

---

## 9. Verificação final

```bash
sudo journalctl -u prospecthor-cockpit -f          # logs em tempo real
curl -s http://127.0.0.1:5055/ | head -c 300       # confirma Flask vivo
```

- Abra `https://cockpit.prospecthor.online` → deve pedir e-mail (Access) → login do dashboard.
- Rode **uma missão de teste** no cockpit e veja os logs (Maps + Fonte B) no painel.

---

## 10. Manutenção e cuidados

- **Atualizar código**: `cd ~/prospecthor && git pull && sudo systemctl restart prospecthor-cockpit prospecthor-scheduler`.
- **Backup do estado** (`data/` guarda estado de missões/status — os leads em si estão no Supabase):
  ```bash
  # cron semanal na VM: 0 4 * * 0 tar -czf ~/backups/data-$(date +\%F).tgz -C ~/prospecthor data
  ```
  E baixe o `.tgz` de tempos em tempos para a sua máquina (`scp`).
- **Limitações do Always Free**: máquinas gratuitas podem ser recicladas pela Oracle
  (dados do *boot volume* persistem, mas faça backup do `data/`), e a capacidade ARM
  varia por região/horário.
- **Fonte A (Google Maps) em IP de datacenter**: risco maior de bloqueio/captcha que o
  seu IP residencial atual. Se cair, reduzir volume diário, usar `REQUEST_DELAY_*` maiores
  ou rodar a Fonte A localmente e só a Fonte B na VM.
- **Plano B pago barato**, se a Oracle não servir: OVHcloud `VPS-2` (~€8,50/mês, 4 vCPU/8 GB)
  ou Hetzner `CPX21/CPX31` — mesmos passos 2–10, só troca o provedor da VM.

---

## Resumo do custo

| Item                          | Custo |
|-------------------------------|-------|
| VM Oracle Always Free (4 OCPU/24 GB) | R$ 0,00 |
| Cloudflare Tunnel + Zero Trust (50 usuários) | R$ 0,00 |
| Supabase (banco)              | já usado |
| Vercel (site)                 | já usado |

**Total: R$ 0,00/mês.**

---

## Links úteis (oficiais)

- Oracle Cloud Always Free (planos/limites): https://www.oracle.com/cloud/free/
- Criar instância na Oracle (guia OCI): https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm
- Instalar o cloudflared (apt oficial): https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
- Criar um tunnel (cloudflared): https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/
- Zero Trust Access (free até 50 usuários): https://developers.cloudflare.com/cloudflare-one/policies/access/
- Playwright em Linux (instalar browsers/deps): https://playwright.dev/docs/browsers
- Instalar Python 3.12+ no Ubuntu: https://www.python.org/ (via apt `python3`)
- GitHub do projeto: https://github.com/pdaugusto/prospecthor
- OVHcloud VPS (plano B pago): https://www.ovhcloud.com/pt/vps/
- Hetzner Cloud (plano B pago): https://www.hetzner.com/cloud/
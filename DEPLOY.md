# 🚀 Instruções de Deploy — Prospector Bot

Este documento fornece as diretrizes passo a passo para implantar e executar o Prospector Bot em diferentes ambientes (Máquina Local, Railway e Oracle Cloud).

---

## 💻 1. Execução Local (Windows / Desenvolvimento)

Siga os comandos abaixo para inicializar o ambiente virtual e rodar o projeto na sua máquina:

1. **Abra o terminal** na pasta do projeto e crie o ambiente virtual:
   ```powershell
   python -m venv venv
   ```
2. **Ative o ambiente virtual**:
   ```powershell
   venv\Scripts\activate
   ```
3. **Instale as dependências** do projeto:
   ```powershell
   pip install -r requirements.txt
   ```
4. **Instale os binários do navegador** necessários para a automação do Playwright:
   ```powershell
   playwright install chromium
   ```
5. **Configure as variáveis de ambiente**:
   - Copie o arquivo `.env.example` para `.env`:
     ```powershell
     copy .env.example .env
     ```
   - Edite o arquivo `.env` configurando sua senha do Dashboard, chave da API do Google (opcional) e horários de agendamento desejados.
6. **Inicie o serviço unificado** (Scheduler em background + Dashboard web):
   ```powershell
   python main.py all
   ```
7. **Acesse no navegador**:
   - URL: [http://localhost:5000](http://localhost:5000)
   - Credenciais: Usuário e senha configurados no seu `.env`.

---

## 🚂 2. Implantação no Railway (Nuvem / PaaS)

O Railway é uma plataforma excelente que detecta e implanta imagens baseadas em Dockerfile de forma automática.

1. **Crie um repositório** privado ou público no GitHub e envie o código do projeto para lá:
   ```bash
   git init
   git add .
   git commit -m "feat: prospector bot release"
   git remote add origin <url-do-repositorio>
   git branch -M main
   git push -u origin main
   ```
2. **Acesse o painel do Railway** ([railway.app](https://railway.app)) e crie um novo projeto.
3. Selecione **"Deploy from GitHub repo"** e dê permissão para o repositório do seu bot.
4. O Railway lerá o arquivo [`Dockerfile`](Dockerfile) automaticamente e configurará o ambiente Python/Playwright.
5. **Defina as variáveis de ambiente** nas configurações do serviço no Railway:
   - Adicione todas as chaves descritas no [`.env.example`](.env.example) (como `DASHBOARD_USER`, `DASHBOARD_PASS` e `DASHBOARD_SECRET_KEY`).
6. **Gere um domínio público** nas configurações do serviço (Railway domain) para obter uma URL HTTPS utilizável.
7. O deploy iniciará. Após finalizado, acesse o link gerado para ver o Dashboard online.

---

## ☁️ 3. Deploy no Oracle Cloud (Infraestrutura Gratuita 24h / IaaS)

A Oracle Cloud oferece instâncias gratuitas no plano *Always Free*, ideal para rodar o robô e o painel continuamente sem custos.

### Pré-requisitos na Instância Oracle Linux / Ubuntu:
- Docker instalado
- Docker Compose instalado
- Portas liberadas no painel de controle da Oracle (Ingress Rules) e no firewall local do Linux.

### Passo a passo no Servidor:

1. **Conecte via SSH** na sua instância da Oracle Cloud:
   ```bash
   ssh -i private_key.key ubuntu@<IP-DO-SERVIDOR>
   ```
2. **Clone seu repositório** ou copie os arquivos do projeto para a máquina virtual:
   ```bash
   git clone <url-do-repositorio>
   cd prospector-bot
   ```
3. **Crie e configure o arquivo `.env`** do servidor:
   ```bash
   cp .env.example .env
   nano .env
   ```
4. **Construa e inicialize os containers** em segundo plano usando o Docker Compose:
   ```bash
   sudo docker-compose up -d --build
   ```
5. **Verifique os logs** para garantir que o agendador e o painel iniciaram com sucesso:
   ```bash
   sudo docker-compose logs -f
   ```
6. **Acesse o Dashboard**:
   - Abra o navegador e digite: `http://<IP-DO-seu-servidor>:5000`
   - O bot rodará 24 horas por dia executando as varreduras de prospecção e qualificando leads locais automaticamente.

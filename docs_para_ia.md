# 📚 Documentação do ProspecThor para Inteligência Artificial (IA)

Esta documentação foi criada para orientar IAs e novos desenvolvedores sobre a arquitetura e estado atual do projeto **ProspecThor**.
O projeto passou por um grande refactoring e deixou de ser um script monolítico com SQLite, tornando-se uma **plataforma SaaS Serverless (Vercel) com Banco em Nuvem (PostgreSQL) e sistema próprio de monetização (Trovoedas).**

---

## 📌 1. A Essência do Negócio
O ProspecThor encontra empresas locais (Google Maps, OpenStreetMap, CNPJs), audita sua presença digital (se tem site, se é rápido, se tem Instagram ativo, se usa SSL) e atribui um **Score de Oportunidade**.
* **Problemas Somam Pontos**: Quanto pior a presença digital da empresa, maior seu score.
* **Leads "Raio"**: O foco primário do sistema são empresas que **não têm site ou usam apenas redes sociais no lugar do site**.
* **Modelo de Venda SaaS**: O cliente acessa um painel na web, compra pacotes de **Trovoedas** (moeda virtual do app) e gasta essas moedas para "desbloquear" os contatos (WhatsApp) dos leads para abordá-los.

---

## 🏗️ 2. Arquitetura e Módulos

O ProspecThor é dividido em dois sub-sistemas independentes e fracamente acoplados, conectados pelo mesmo banco PostgreSQL no Supabase:

### A. O Dashboard SaaS (Vercel)
Onde os clientes e o dono do sistema ("Patrão") acessam os dados.
- **Ponto de entrada:** `/api/index.py` (Script Flask adaptado para execução Serverless).
- **Hospedagem:** Vercel (Configurado em `vercel.json`).
- **Peculiaridades:** Sem dependência de estado em memória local. Sessões persistidas via cookies seguros. Rotas focadas em mostrar leads, comprar pacotes de Trovoedas (em `/src/trovoeda.py`), sistema de auditoria e configurações de usuários.
- **Isolamento:** O cliente só visualiza leads que estão designados (`assigned_to`) a ele. O Patrão vê o "pool" (as sobras) ou todos os leads.

### B. O Motor de Coleta & Cockpit (Máquina Local / VM Dedicada)
Onde as raspagens acontecem e o banco de dados é alimentado.
- **Cockpit (`/cockpit/app.py`):** Servidor Flask local que roda na porta `5055` (iniciado via `Abrir-Cockpit.bat`). Funciona como um painel de controle do Patrão para dar start/stop nas buscas, ver logs ao vivo, definir missões e acompanhar quantos leads foram gerados.
- **Worker (`main.py` e `/src/`):** Os scripts que são orquestrados pelo Cockpit. Realizam navegação com Playwright (`src/instagram_checker.py`), requests (`src/website_checker.py`), consultas de coordenadas e Google (`src/google_maps.py`), avaliação e inserção no Postgres (`src/scorer.py`, `src/users.py`).

---

## 📂 3. Mapa de Pastas e Arquivos Principais

* **`/api/index.py`**: O coração do painel web. Contém a inicialização do Flask, rotas de login/registro, listagem de leads (Dashboard Vercel).
* **`/cockpit/`**: Pasta da aplicação local. `app.py` orquestra os processos em background (`subprocess.Popen`) rodando o `main.py`.
* **`/config/`**: JSONs (`cities.json`, `niches.json`) com os parâmetros de varredura das cidades e categorias.
* **`/src/`**: Lógica de negócio, scrapers e módulos de acesso a banco.
  - `scorer.py`: Cálculos de nota e injeção do detalhamento de problemas da empresa.
  - `users.py`: Toda a gestão de `app_users` e `companies` (atribuição de leads).
  - `trovoeda.py`: Lógica da carteira do usuário (desconto, pacotes, histórico).
  - `bot_status.py` / `bot_plan.py`: Comunica o status da raspagem para o Cockpit (meta, contagem, status).
* **`/templates/` & `/static/`**: HTML/CSS/JS utilizados pela interface. O layout foca em um design de alta conversão.

---

## 🔧 4. Dicas e Convenções Técnicas
- **Banco de Dados**: Usamos `psycopg2` para todas as querys no PostgreSQL. As migrations (criação de colunas) geralmente rodam implicitamente se a coluna faltar (em blocos `ensure_schema` espalhados nos arquivos).
- **Sem ORM pesado**: As consultas ao banco são feitas através de SQL puro com `RealDictCursor` para retornar dicionários compatíveis com JSON.
- **Usuário Admin Fixo**: A lógica frequentemente se apoia em conferir se o username logado é `"patrao"` ou o ID da sessão. O dono do SaaS atua sempre como "patrao".
- **Comandos**: 
  - Para rodar web local: `python api/index.py`
  - Para rodar Cockpit local: `python cockpit/app.py` ou `.bat`
- **Variáveis de Ambiente**: Estão no arquivo `.env` (onde fica a `DATABASE_URL`, chaves do Google, senhas de painel). Não versione o `.env`.

---

## 💡 Como a IA Pode Ajudar
Quando receber solicitações de alterações no ProspecThor:
1. Identifique se o pedido é referente ao **SaaS/Painel Web** (foco no `/api/index.py` e `/templates`) ou ao **Motor de Busca/Cockpit** (foco no `/cockpit` e `/src`).
2. Mantenha as consultas em `psycopg2` preparadas para evitar SQL Injection (uso de `%s` no execute).
3. Lembre-se que o painel roda de forma efêmera e Serverless (não armazene estado em variáveis globais no `index.py`, use o banco de dados).

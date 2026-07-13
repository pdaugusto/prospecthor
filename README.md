<<<<<<< HEAD
# 🤖 Prospector Bot
> **Bot de prospecção automatizada de leads com presença digital fraca.**

O **Prospector Bot** é uma ferramenta de inteligência comercial desenvolvida para identificar empresas locais, qualificar a presença digital delas (site, redes sociais, perfil do Google, cardápios) e gerar listas qualificadas de leads para times de vendas. O seu motor de busca e pontuação prioriza empresas com **baixo desempenho digital**, pois estas representam as maiores oportunidades de vendas de serviços de desenvolvimento de sites, gestão de tráfego, design e SEO.

---

## 🔄 1. Fluxo de Funcionamento

```text
  [ Início ]
      │
      ▼
┌──────────────┐
│  Google Maps │ ◄── Varre e coleta dados de empresas (API/Scraping Playwright)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ website      │ ◄── Verifica SSL, responsividade móvel e velocidade de carregamento
│ checker      │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ instagram    │ ◄── Analisa seguidores, contagem de posts e regularidade da timeline
│ checker      │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ menu         │ ◄── Identifica a presença de cardápios no site, insta ou iFood/Rappi
│ checker      │
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ lead scorer  │ ◄── Soma pontuações (presença fraca = pontuação alta!)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Banco SQLite │ ◄── Salva os leads qualificados ordenados por maior prioridade
└──────────────┘
```

---

## 🏗️ 2. Diagrama de Arquitetura

O bot opera de forma desacoplada através do banco de dados SQLite, permitindo que a varredura em background e a interface de visualização coexistam de forma segura e paralela:

```text
 ┌──────────────────────┐             ┌──────────────────────┐
 │    Background Bot    │             │   Dashboard Web      │
 │  (scheduler/threads) │             │    (Flask / API)     │
 └──────────┬───────────┘             └──────────┬───────────┘
            │                                    │
            │  Escreve Leads                     │  Lê Estatísticas
            │  Qualificados                      │  e Abordagens
            ▼                                    ▼
       ┌──────────────────────────────────────────────┐
       │             Banco de Dados Local             │
       │                 (SQLite WAL)                 │
       └──────────────────────────────────────────────┘
```

---

## 📋 3. Pré-requisitos

Para rodar o projeto localmente, certifique-se de ter instalado:
* **Python 3.11** ou superior
* **Playwright** (automação e renderização de browsers)
* **Navegador Chromium** instalado via CLI do Playwright

---

## 🚀 4. Instalação Passo a Passo (Windows)

1. **Clone o repositório** para sua máquina local:
   ```bash
   git clone https://github.com/seu-usuario/prospector-bot.git
   cd prospector-bot
   ```

2. **Crie o ambiente virtual**:
   ```bash
   python -m venv venv
   ```

3. **Ative o ambiente virtual**:
   ```bash
   venv\Scripts\activate
   ```

4. **Instale as dependências**:
   ```bash
   pip install -r requirements.txt
   ```

5. **Instale o navegador de automação do Playwright**:
   ```bash
   playwright install chromium
   ```

6. **Configure as variáveis de ambiente**:
   - Copie o arquivo modelo:
     ```bash
     copy .env.example .env
     ```
   - Edite o arquivo `.env` gerado e defina suas credenciais personalizadas de login do dashboard.

---

## 🕹️ 5. Como Rodar o Bot (CLI)

O Prospector Bot vem com uma interface de linha de comando completa:

```bash
# Modo principal: Inicia o Scheduler (busca automática) + Dashboard Web juntos
python main.py all

# Executa uma varredura completa sob demanda agora
python main.py run

# Executa uma busca manual específica
python main.py run --niche restaurante --cidade "Porto Alegre" --estado RS

# Executa apenas o Scheduler diário em background
python main.py schedule

# Inicia apenas o servidor web do Dashboard (porta 5000)
python main.py dashboard

# Exibe o status consolidado do bot, contagem de leads e logs de execução
python main.py status

# Exporta leads qualificados para arquivo CSV
python main.py export --tipo quentes
python main.py export --cidade "Curitiba"
```

---

## 📂 6. Estrutura de Pastas

```text
prospector-bot/
├── config/
│   ├── cities.json           # Cidades ativas para busca e suas coordenadas
│   ├── niches.json           # Categorias de negócios configuradas
│   └── settings.py           # Centralizador de carregamento do arquivo .env
├── data/
│   └── exports/              # Diretório de saída dos relatórios CSV/Excel
├── src/
│   ├── google_maps.py        # Coletor base da API ou Scraping Google Maps
│   ├── website_checker.py    # Qualificação de site próprio, SSL e velocidade
│   ├── instagram_checker.py  # Descoberta de handle e auditoria de feed
│   ├── menu_checker.py       # Identificação de cardápios e apps de delivery
│   ├── scorer.py             # Lógica de cálculo de pontuação de oportunidade
│   ├── exporter.py           # Converte dados do SQLite para planilhas
│   └── scheduler.py          # APScheduler das tarefas recorrentes diárias
├── templates/
│   ├── login.html            # Interface de login administrativo
│   └── index.html            # Interface administrativa Single Page Application (SPA)
├── dashboard.py              # Backend REST API do painel administrativo Flask
├── main.py                   # CLI centralizador do projeto
├── requirements.txt          # Dependências do projeto
├── Dockerfile                # Imagem de produção do container
├── docker-compose.yml        # Orquestrador do container com volumes
└── README.md                 # Este documento
```

---

## 🧠 7. Módulos do Sistema

* **[`main.py`](main.py)**: CLI unificado do sistema. Conecta comandos, faz o bind de logging e gerencia a inicialização.
* **[`google_maps.py`](src/google_maps.py)**: Responsável por descobrir as empresas locais. Utiliza requisições oficiais via Google Places API com fallback em tempo de execução via scraping headless do Google Maps se a chave de API não estiver disponível.
* **[`website_checker.py`](src/website_checker.py)**: Faz requisições HTTP para analisar se o site existe, se é rápido, se possui HTTPS ativo, viewport de design responsivo e contatos visíveis.
* **[`instagram_checker.py`](src/instagram_checker.py)**: Descobre perfis corporativos e usa Playwright mobile para colher métricas públicas (seguidores, postagens recentes) sem requerer login.
* **[`menu_checker.py`](src/menu_checker.py)**: Identifica se estabelecimentos alimentícios possuem soluções digitais de vendas próprias ou se dependem unicamente de iFood/Rappi.
* **[`scorer.py`](src/scorer.py)**: Aplica o algoritmo de pesos e qualifica os leads em prioridades, gerando uma lista de problemas e serviços que podem ser vendidos para a empresa.
* **[`scheduler.py`](src/scheduler.py)**: Monitora e executa de forma automática em segundo plano as rotinas diárias com base nas configurações e rotaciona as cidades cadastradas.
* **[`exporter.py`](src/exporter.py)**: Módulo de suporte a CRM que extrai leads e monta arquivos em formato CSV com cabeçalhos amigáveis e codificação UTF-8-BOM.

---

## 📊 8. Algoritmo de Pontuação de Leads

O lead acumula pontos de **0 a 150**. Lembrar: **mais pontos = presença digital mais fraca = maior chance de venda de serviços**.

| Categoria | Critério Mapeado | Pontos Adicionados |
|---|---|:---:|
| **Site** | Sem site cadastrado | **+30** |
| **Site** | O link cadastrado é apenas rede social (Facebook/Instagram) | **+25** |
| **Site** | Site oficial fora do ar / erro HTTP 5xx | **+20** |
| **Site** | Site em construção / template de WordPress genérico | **+15** |
| **Site** | Site oficial não mobile-friendly (sem viewport meta tag) | **+10** |
| **Site** | Conexão insegura sem certificado SSL (sem HTTPS) | **+5** |
| **Instagram** | Empresa sem conta localizada | **+25** |
| **Instagram** | Perfil parado sem novas postagens há mais de 3 meses | **+20** |
| **Instagram** | Perfil inativo sem postagens há mais de 6 meses | **+15** |
| **Instagram** | Perfil com poucos seguidores ou conta fraca | **+10** |
| **Instagram** | Perfil sem texto de apresentação na Biografia | **+5** |
| **Instagram** | Perfil sem link de contato/vendas na Biografia | **+5** |
| **Cardápio** | Restaurante sem nenhuma opção de cardápio digital | **+20** |
| **Cardápio** | Cardápio desatualizado ou defasado | **+15** |
| **Cardápio** | Cardápio disponível apenas no iFood/Rappi (taxas terceirizadas) | **+10** |
| **Cardápio** | Cardápio incompleto (apenas imagens, sem preços/descrições) | **+10** |
| **Google** | Nota geral do Google Maps abaixo de 3.5 | **+15** |
| **Google** | Nota geral do Google Maps abaixo de 4.0 | **+10** |
| **Google** | Ficha do Google Maps com menos de 5 avaliações locais | **+10** |
| **Google** | Ficha do Google Maps com menos de 10 avaliações locais | **+5** |

---

## 🖥️ 9. Dashboard Administrativo

O painel administrativo consolida a visualização e oferece ferramentas de abordagem para prospecção ativa.

* **Acesso**: Navegue para **[http://localhost:5000](http://localhost:5000)** após iniciar o servidor.
* **Autenticação**: Insira o usuário e a senha cadastrados no seu arquivo `.env` (Padrão: `admin` / `senha123`).
* **Abordagem de Leads**:
  - Clique em qualquer lead da tabela para visualizar o painel técnico de detalhes.
  - O painel exibe a pontuação total, lista com ícones e pesos de cada problema detectado e sugestões de serviços comerciais.
  - Copie os dados consolidados da empresa formatados para a área de transferência para agilizar abordagens de cold call ou cold mail.
  - Altere o status comercial (*novo*, *contactado*, *convertido* ou *descartado*) e salve notas de feedback do cliente.
  - Exporte planilhas customizadas aplicando os filtros dinâmicos de classificação, status, nicho e cidade diretamente do cabeçalho.

---

## ⚙️ 10. Configuração do Scheduler

O agendador diário é modular e configurável. Os horários e nichos padrão são disparados conforme as definições do arquivo de configurações:

* **08:00 (Alimentação)**: `restaurante, bar, pizzaria, hamburgueria, cafeteria`
* **10:00 (Estética)**: `salão de beleza, barbearia, estética, manicure`
* **14:00 (Saúde)**: `clínica, dentista, psicólogo, nutricionista, fisioterapia`
* **16:00 (Comércio)**: `loja de roupa, pet shop, academia, oficina mecânica`
* **20:00 (Outros)**: `escola, curso, imobiliária, escritório de advocacia`
* **22:00 (Relatório)**: Consolida estatísticas gerais no SQLite.

### Customizações:
- **Cidades**: Adicione ou remova cidades alterando a lista no arquivo [`config/cities.json`](config/cities.json). O robô rotacionará automaticamente as cidades ativas a cada dia do ano.
- **Nichos**: Ajuste os multiplicadores e termos de busca no arquivo [`config/niches.json`](config/niches.json).

---

## 🚂 11. Deploy no Railway

O Railway executa deploys contínuos baseados em Dockerfiles de forma transparente:

1. Suba o código do seu repositório para o **GitHub**.
2. Conecte sua conta do GitHub ao painel do **[Railway.app](https://railway.app)**.
3. Crie um novo projeto e selecione o repositório do Prospector Bot.
4. Adicione as variáveis de ambiente necessárias em *Settings / Variables* no Railway:
   - `DASHBOARD_USER`, `DASHBOARD_PASS`, `DASHBOARD_SECRET_KEY` e `DATABASE_PATH=data/leads.db`.
5. O Railway gerará a imagem Docker, instalará as dependências do Playwright e liberará um link de acesso HTTPS público para o painel.

---

## ☁️ 12. Deploy no Oracle Cloud (Always Free)

Rode o Prospector Bot 24 horas por dia em uma máquina virtual Linux gratuita da Oracle:

1. Acesse o terminal da sua instância via SSH:
   ```bash
   ssh -i chave.key ubuntu@<IP_DA_ORACLE_VM>
   ```
2. Clone o repositório do seu bot e acesse o diretório:
   ```bash
   git clone <url-do-repositorio> && cd prospector-bot
   ```
3. Crie e ajuste o arquivo de variáveis de ambiente `.env`:
   ```bash
   cp .env.example .env && nano .env
   ```
4. Suba o ambiente em background usando o docker-compose:
   ```bash
   sudo docker-compose up -d --build
   ```
5. O painel estará disponível no IP público do seu servidor na porta **5000** (certifique-se de liberar a porta 5000 nas *Ingress Rules* da subnet na console Oracle).

---

## 🔧 13. Troubleshooting (Problemas Comuns)

#### 1. Playwright não abre o Chromium
* **Sintoma**: Erro de biblioteca compartilhada no Linux ou falha de inicialização.
* **Causa**: Falta de dependências de sistema no SO ou browsers desatualizados.
* **Solução**: Execute `playwright install chromium` no seu terminal. Em ambientes Linux Debian/Ubuntu fora de containers, execute `sudo playwright install-deps chromium` para instalar as bibliotecas compartilhadas necessárias.

#### 2. Google bloqueou as buscas (CAPTCHA)
* **Sintoma**: Logs de erro de tráfego incomum exibidos pelo Playwright.
* **Solução**: O robô detecta o bloqueio, pausa a execução por 60 segundos e reinicia automaticamente em background de forma transparente. Se os bloqueios forem persistentes, configure a variável `GOOGLE_MAPS_API_KEY` no seu `.env` para usar a API oficial em vez do scraping visual.

#### 3. Banco de dados corrompido ou travado
* **Sintoma**: Mensagem `database is locked` ao tentar ler ou salvar dados.
* **Solução**: O banco de dados do Prospector Bot já utiliza o **WAL Mode** (`Write-Ahead Logging`) ativado por padrão para permitir escritas e leituras concorrentes e evitar travas. Caso ocorra erro físico, remova os arquivos temporários `data/leads.db-wal` ou apague o arquivo de banco para o bot recriar a estrutura do zero.

#### 4. Dashboard não carrega ou exibe erro 401/403
* **Sintoma**: Redirecionamentos em loop ou tela de erro.
* **Solução**: O painel expira sessões antigas ou inválidas. Limpe os cookies do navegador para a URL local e faça login novamente usando os dados do seu `.env`.

---

## 🗺️ 14. Roadmap

* [ ] Integração com WhatsApp Web API para disparos de mensagens automáticas a leads quentes.
* [ ] Integração com SMTP/Sendgrid para automação de campanhas de e-mail marketing (cold mail).
* [ ] Suporte a múltiplos usuários e controle de acessos (ACL/Permissões) no Dashboard.
* [ ] Endpoint público e chaves de API para integrações com ferramentas No-code (Zapier, Make, n8n).
* [ ] Sincronização automatizada bidirecional com CRMs populares (HubSpot, RD Station e Pipedrive).

---

## 🛠️ 15. Tecnologias Utilizadas

* **Python 3.11** — Linguagem e motor central do sistema.
* **Playwright** — Automação e extração de redes sociais e ficha local de forma headless.
* **BeautifulSoup & lxml** — Parser rápido e otimizado de marcações HTML de websites.
* **SQLite (WAL Mode)** — Banco de dados local relacional rápido.
* **Flask** — Servidor de API leve.
* **Chart.js** — Biblioteca de gráficos interativos no front-end.
* **Loguru** — Logging avançado formatado com rotação diária de arquivos.

---

## 📄 16. Licença

Este projeto está licenciado sob os termos da licença **MIT**. Veja o arquivo `LICENSE` para mais detalhes.
=======
# prospecthor
>>>>>>> 1c229396a7dfeba0da6d38d083590beb544a386b

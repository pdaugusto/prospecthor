# 🚀 ProspecThor

> **SaaS de prospecção automatizada de leads corporativos locais e venda de oportunidades comerciais.**

O **ProspecThor** evoluiu de uma ferramenta de uso interno para um **SaaS completo**, com banco de dados em nuvem, painel online para clientes, e um motor autônomo (Cockpit) de enriquecimento de leads.
Sua inteligência foca em encontrar negócios físicos com **baixa ou nenhuma presença digital** (sem site, sem Instagram, sites lentos ou sem SSL) – oportunidades perfeitas (os chamados leads "Raio") para agências, gestores de tráfego e desenvolvedores web.

---

## 🔄 1. Como o Ecossistema Funciona

O projeto agora é dividido em duas partes principais:

### 🤖 1.1. Os Robôs de Coleta (Local / Servidor Dedicado)
Os scripts locais fazem o trabalho pesado. Eles buscam CNPJs, mapas (Google Maps, OSM) e redes sociais para encontrar novas empresas.
- **Cockpit do Patrão (`cockpit/app.py`)**: Uma interface web local (porta 5055) onde o administrador configura nichos, cidades e inicia/para o robô (worker) sem precisar usar terminal.
- **Checkers (`src/`)**: Módulos que validam se a empresa tem site responsivo, se o Instagram está abandonado, etc.
- Tudo que o robô coleta e qualifica é salvo em um banco **PostgreSQL em nuvem (Supabase)**.

### 🌐 1.2. O Dashboard SaaS (Vercel)
O painel onde os clientes finais entram. Totalmente serverless, rodando no **Vercel** (`api/index.py`).
- **Trovoedas (Moeda Própria)**: Clientes compram "Trovoedas" (moeda virtual do ProspecThor) em pacotes.
- **Desbloqueio de Leads**: Cada lead interessante encontrado pode ser desbloqueado (revelando o WhatsApp/Contato) descontando Trovoedas do saldo do cliente.
- **Isolamento de Dados**: Os clientes só veem os contatos que eles desbloquearam. Apenas o admin (Patrão) vê a piscina (pool) inteira.

---

## 🏗️ 2. Arquitetura Atual e Tecnologias

O sistema agora é totalmente distribuído:

```text
 ┌──────────────────────┐             ┌──────────────────────┐
 │   Cockpit do Patrão  │             │   Dashboard SaaS     │
 │  (Worker Local - PC) │             │ (Vercel Serverless)  │
 └──────────┬───────────┘             └──────────┬───────────┘
            │                                    │
            │  Escreve Leads                     │  Vende Leads,
            │  via psycopg2                      │  Gere Usuários e
            │                                    │  Saldo de Trovoedas
            ▼                                    ▼
       ┌──────────────────────────────────────────────┐
       │           PostgreSQL Cloud (Supabase)        │
       │           (Centraliza toda a operação)       │
       └──────────────────────────────────────────────┘
```

* **Backend / API**: Python 3.11+, Flask.
* **Deploy Cloud**: Vercel Serverless (`vercel.json` roteando para `/api/index.py`).
* **Banco de Dados**: PostgreSQL (migrado de SQLite).
* **Automação**: Playwright (para scraping avançado de redes sociais).

---

## 📋 3. Pré-requisitos para Desenvolvimento

* **Python 3.11** ou superior
* **Playwright** instalado
* **PostgreSQL** em nuvem (ex: Supabase) com as variáveis no `.env`.

---

## 🚀 4. Instalação e Execução

### Para rodar os robôs de coleta e o Cockpit (Máquina do Administrador):

1. **Clone e instale**:
   ```bash
   git clone <repo>
   cd prospector-bot
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **Configure o `.env`**:
   Copie o `.env.example` e adicione sua `DATABASE_URL` do Supabase.

3. **Inicie o Cockpit**:
   ```bash
   Abrir-Cockpit.bat
   # ou: python cockpit/app.py
   ```
   Acesse **http://localhost:5055** para mandar os robôs trabalharem.

### Para testar o Dashboard da Vercel Localmente:

O painel SaaS é construído como uma aplicação Flask compatível com Vercel (arquitetura em `api/index.py`).
Para rodar:
```bash
python api/index.py
```
Acesse **http://localhost:5000**. Usuário administrador fixo no código/BD é `patrao`.

---

## 📂 5. Estrutura de Pastas Simplificada

* **`/api`**: Contém o `index.py`, ponto de entrada principal pro Dashboard SaaS na Vercel.
* **`/cockpit`**: Interface visual local de controle dos robôs.
* **`/config`**: Configurações de nichos, cidades, limites e setups diversos.
* **`/src`**: Todo o coração da prospecção (google_maps, instagram_checker, scorer, trovoeda, etc.).
* **`/templates` & `/static`**: HTML, CSS, e JS usados tanto pelo Dashboard quanto pelo Cockpit.
* **`main.py`**: Ponto de entrada CLI/backend usado pelos workers do Cockpit.
* **`vercel.json`**: Diretiva de rotas de deploy para a Vercel.

---

## 📊 6. Sistema de Score e Leads "Raio"

O objetivo do ProspecThor não é achar empresas famosas, mas empresas com **muito a melhorar no digital**.
Cada problema identificado soma pontos ao **Lead Score**:
- Sem site? **+30 pts**
- Instagram sem publicações? **+20 pts**
- Avaliação baixa no Google? **+15 pts**

Além disso, a plataforma foca fortemente nos chamados leads **"Raio"**, que são empresas que não possuem site próprio (ou usam link de instagram/whatsapp onde deveria estar o site). Estes são filtrados primariamente na tela inicial.

---

## 📄 7. Licença

Este projeto é de uso privado e comercial. Todos os direitos reservados.

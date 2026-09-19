# ⚡ ProspecTHOR — Instruções de Instalação e Execução

Este guia contém as instruções passo a passo para configurar e rodar o projeto **ProspecTHOR** em qualquer computador do zero.

---

## 📂 1. Estrutura do Projeto Organizada

Para facilitar o seu trabalho, o projeto no seu disco `D:\Prospecthor` está dividido da seguinte forma:

*   `api/`: Contém o ponto de entrada principal do servidor Flask em produção (Vercel). O arquivo principal é o `index.py`.
*   `src/`: Lógica de negócio do projeto, como conexão com o banco de dados, regras de cotas de leads e balanceamento de Trovoedas (`users.py`, `trovoeda.py`, `scorer.py`).
*   `cockpit/`: O **Cockpit Local** de automação. É a ferramenta responsável por rodar o robô de raspagem (scraper) com Playwright.
*   `templates/` & `static/`: O frontend (HTML, CSS e JS) do painel do usuário e da loja.
*   `data/`: Pasta para armazenamento de estados, backups locais e arquivos temporários.
*   `scripts/`: Scripts utilitários de manutenção e testes do banco de dados.

---

## 💻 2. Requisitos de Instalação (Novo Computador)

Para rodar este projeto em outro computador, você precisará instalar:

1.  **Python 3.10 ou superior**: Baixe e instale a versão mais recente para Windows pelo site oficial (certifique-se de marcar a opção *"Add Python to PATH"* durante a instalação).
2.  **Git (Opcional)**: Para versionamento e envio de commits para o GitHub.

---

## 🛠️ 3. Passo a Passo de Configuração

Abra o prompt de comando (CMD ou PowerShell) na raiz da pasta do projeto (`D:\Prospecthor`) e siga os comandos abaixo:

### Passo 3.1: Criar o Ambiente Virtual (venv)
O ambiente virtual isola as bibliotecas do projeto para que não dê conflito no computador.
```bash
python -m venv venv
```

### Passo 3.2: Ativar o Ambiente Virtual
*   No **Windows** (CMD):
    ```cmd
    venv\Scripts\activate.bat
    ```
*   No **Windows** (PowerShell):
    ```powershell
    venv\Scripts\Activate.ps1
    ```
*   No **macOS/Linux**:
    ```bash
    source venv/bin/activate
    ```

### Passo 3.3: Instalar as Dependências do Python
Com o ambiente virtual ativo, instale todas as bibliotecas necessárias listadas no `requirements.txt`:
```bash
pip install -r requirements.txt
```

### Passo 3.4: Instalar os Navegadores do Playwright
O robô de busca de leads precisa do motor de navegação para raspar os dados. Instale os navegadores com o comando:
```bash
playwright install
```

---

## 🔑 4. Configurando as Variáveis de Ambiente (`.env`)

O arquivo `.env` armazena as chaves de acesso confidenciais do projeto (Banco de dados, Stripe, etc.). O arquivo já está criado no seu disco `D:\Prospecthor\.env`, mas se você for rodar em outra máquina, **deve criar um arquivo exatamente com o nome `.env`** e preenchê-lo com a seguinte estrutura:

```env
# URL de conexão com o banco de dados do Supabase
DATABASE_URL=postgresql://postgres.orijpeorinimxfzcnhsa:raiosetrovoes@aws-0-us-east-1.pooler.supabase.com:5432/postgres

# Usuário de acesso administrativo do Dashboard (Patrão)
DASHBOARD_USER=patrao
DASHBOARD_PASS=SuaSenhaAqui

# Chave API Secreta do Stripe (Chave de Testes ou Produção)
STRIPE_API_KEY=sk_test_...

# Segredo de assinatura do Webhook do Stripe (Chave para confirmar pagamentos)
STRIPE_WEBHOOK_SECRET=whsec_...
```

> 💡 **Dica de Produção**:
> Para mudar o site para aceitar pagamentos reais, basta trocar a `STRIPE_API_KEY` pela chave secreta que começa com `sk_live_...` e o `STRIPE_WEBHOOK_SECRET` pelo segredo do webhook de produção gerado no Stripe.

---

## 🚀 5. Como Executar as Ferramentas

### 🎛️ Rodando o Cockpit (Robô de Raspagem)
O Cockpit serve para você iniciar e gerenciar a raspagem de leads no Google Maps de forma visual e local.

*   **Pelo atalho do Windows**: Basta clicar duas vezes no arquivo `Abrir-Cockpit.bat` na raiz da pasta do projeto.
*   **Pelo terminal (Venv ativo)**:
    ```bash
    python cockpit/start.py
    ```
    *Acesse o painel local pelo navegador em: **`http://localhost:5055`***

### 🌐 Rodando o Site do Cliente Localmente
Caso queira testar as páginas do site ou a loja no seu próprio computador antes de subir para a Vercel:

*   **Pelo terminal (Venv ativo)**:
    ```bash
    python api/index.py
    ```
    *Acesse pelo navegador em: **`http://localhost:5000`***

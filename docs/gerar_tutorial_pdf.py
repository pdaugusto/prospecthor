"""Gera o PDF: Tutorial ProspecTHOR — setup multi-notebook."""
from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

OUT = Path(__file__).resolve().parent / "ProspecTHOR-Tutorial-Setup-Multi-Notebook.pdf"


def _s(t: str) -> str:
    """Helvetica core font: latin-1 only."""
    if t is None:
        return ""
    repl = {
        "\u2014": "-",
        "\u2013": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u00e7": "c",
        "\u00e3": "a",
        "\u00e1": "a",
        "\u00e0": "a",
        "\u00e2": "a",
        "\u00e9": "e",
        "\u00ea": "e",
        "\u00ed": "i",
        "\u00f3": "o",
        "\u00f4": "o",
        "\u00f5": "o",
        "\u00fa": "u",
        "\u00fc": "u",
        "\u00c7": "C",
        "\u00c3": "A",
        "\u00c1": "A",
        "\u00c9": "E",
        "\u00cd": "I",
        "\u00d3": "O",
        "\u00da": "U",
    }
    for a, b in repl.items():
        t = t.replace(a, b)
    return t.encode("latin-1", errors="replace").decode("latin-1")


class Doc(FPDF):
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(100, 110, 120)
        self.cell(0, 8, _s("ProspecTHOR - Tutorial de setup (multi-notebook)"), align="L")
        self.ln(10)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(130, 140, 150)
        self.cell(0, 8, f"Pagina {self.page_no()}/{{nb}}", align="C")

    def _reset_x(self) -> None:
        self.set_x(self.l_margin)

    def h1(self, t: str) -> None:
        self._reset_x()
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(10, 20, 35)
        self.multi_cell(0, 9, _s(t))
        self.ln(3)

    def h2(self, t: str) -> None:
        self.ln(2)
        self._reset_x()
        self.set_font("Helvetica", "B", 13)
        self.set_text_color(0, 120, 160)
        self.multi_cell(0, 7, _s(t))
        self.ln(1)

    def h3(self, t: str) -> None:
        self.ln(1)
        self._reset_x()
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(30, 40, 55)
        self.multi_cell(0, 6, _s(t))
        self.ln(0.5)

    def body(self, t: str) -> None:
        self._reset_x()
        self.set_font("Helvetica", "", 10)
        self.set_text_color(25, 30, 40)
        self.multi_cell(0, 5.2, _s(t))
        self.ln(1.5)

    def bullet(self, t: str) -> None:
        self._reset_x()
        self.set_font("Helvetica", "", 10)
        self.set_text_color(25, 30, 40)
        self.multi_cell(0, 5.2, _s(f"  -  {t}"))

    def code(self, t: str) -> None:
        self._reset_x()
        self.set_fill_color(240, 244, 248)
        self.set_font("Courier", "", 8.5)
        self.set_text_color(20, 30, 40)
        self.multi_cell(0, 4.5, _s(t), fill=True)
        self.ln(2)

    def box(self, title: str, text: str) -> None:
        self._reset_x()
        self.set_fill_color(230, 245, 252)
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(0, 90, 120)
        self.multi_cell(0, 6, _s(title), fill=True)
        self._reset_x()
        self.set_font("Helvetica", "", 9.5)
        self.set_text_color(30, 40, 50)
        self.set_fill_color(245, 250, 253)
        self.multi_cell(0, 5, _s(text), fill=True)
        self.ln(2)


def build() -> Path:
    pdf = Doc()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_margins(16, 16, 16)

    # CAPA
    pdf.ln(20)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(5, 15, 30)
    pdf.cell(0, 12, "ProspecTHOR", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(0, 160, 200)
    pdf.cell(0, 8, "Tutorial de setup multi-notebook", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(60, 70, 80)
    pdf.multi_cell(
        0,
        6,
        "Como ter o projeto em outro notebook, rodar o bot,\n"
        "usar o cockpit e servir clientes em outra cidade.",
        align="C",
    )
    pdf.ln(10)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(
        0,
        5,
        "Documento interno - operacao do Patrao (nao e o painel do cliente).",
        align="C",
        new_x="LMARGIN",
        new_y="NEXT",
    )

    # 1 VISÃO
    pdf.add_page()
    pdf.h1("1. Visao do projeto final")
    pdf.body(
        "O ProspecTHOR tem DUAS partes diferentes. Misturar as duas e a principal fonte de confusao."
    )
    pdf.h3("A) Painel web do cliente (site / Vercel)")
    pdf.bullet("Cliente entra com login e ve SO os leads dele.")
    pdf.bullet("Voce (patrao) ve sobras, usuarios, cotas, status.")
    pdf.bullet("Roda na nuvem (Vercel + banco Postgres).")
    pdf.bullet("NAO roda o robô do Google Maps.")
    pdf.ln(1)
    pdf.h3("B) Bot + Cockpit (sua maquina)")
    pdf.bullet("Varre o Maps, grava leads no banco, pontua score.")
    pdf.bullet("Cockpit = painel local so seu (fila de missoes, Run, Parar, Score).")
    pdf.bullet("Precisa de Python + Playwright no PC/notebook.")
    pdf.ln(1)
    pdf.box(
        "Regra de ouro",
        "Cliente em outra cidade NAO precisa do bot no notebook dele.\n"
        "Ele so usa o SITE (login). O bot pode rodar no SEU notebook (ou num PC fixo),\n"
        "desde que o .env aponte pro MESMO DATABASE_URL do site.",
    )

    # 2 CENÁRIOS
    pdf.h1("2. Cenarios reais")
    pdf.h2("Cenario 1 — Voce opera tudo (recomendado agora)")
    pdf.bullet("Notebook A (seu): bot + cockpit + codigo.")
    pdf.bullet("Cliente (qualquer cidade): so abre o site no celular/PC e trabalha os leads.")
    pdf.bullet("Banco e unico (Supabase/Postgres). Todos veem o mesmo mundo, com isolamento por conta.")
    pdf.ln(1)
    pdf.h2("Cenario 2 — Voce tem 2 notebooks")
    pdf.bullet("Copia o projeto nos dois (Git).")
    pdf.bullet("MESMO arquivo .env (mesmo DATABASE_URL).")
    pdf.bullet("Nao rode o bot nos DOIS ao mesmo tempo (conflito de fila/Maps).")
    pdf.bullet("Use um como principal; o outro e backup ou so cockpit/leitura.")
    pdf.ln(1)
    pdf.h2("Cenario 3 — Futuro SaaS (varios clientes pagantes)")
    pdf.bullet("Painel continua na Vercel.")
    pdf.bullet("Bot idealmente em 1 maquina 24h (PC fixo ou VPS).")
    pdf.bullet("Cockpit so seu; cliente nunca ve o cockpit.")

    # 3 O QUE BAIXAR
    pdf.add_page()
    pdf.h1("3. O que precisa baixar / instalar (notebook novo)")
    pdf.h3("Obrigatorio")
    pdf.bullet("Git — https://git-scm.com/download/win")
    pdf.bullet("Python 3.11 ou 3.12 — https://www.python.org/downloads/")
    pdf.bullet("   (marque 'Add Python to PATH' na instalacao)")
    pdf.bullet("Conta no GitHub com acesso ao repositorio do projeto")
    pdf.bullet("Arquivo .env (voce copia do notebook antigo — NUNCA sobe pro Git)")
    pdf.ln(1)
    pdf.h3("Opcionais")
    pdf.bullet("VS Code ou Cursor (editar codigo)")
    pdf.bullet("Chrome (o Playwright instala o Chromium sozinho)")
    pdf.ln(1)
    pdf.box(
        "Importante sobre o .env",
        "Sem o .env com DATABASE_URL o bot grava em lugar nenhum util.\n"
        "Tambem precisa de DASHBOARD_PASS / secrets se for rodar login local.\n"
        "Trate o .env como senha: pen-drive, pasta privada, 1Password — nunca Discord publico.",
    )

    # 4 PASSO A PASSO
    pdf.h1("4. Passo a passo — clonar no notebook novo")
    pdf.h2("Passo 1 — Abrir o PowerShell na pasta que quiser")
    pdf.body("Exemplo de pasta curta (recomendada):")
    pdf.code("cd C:\\bots")
    pdf.h2("Passo 2 — Clonar o repositorio")
    pdf.code(
        "git clone https://github.com/pdaugusto/prospecthor.git\n"
        "cd prospecthor"
    )
    pdf.body("(Se o repo for privado, o Git vai pedir login do GitHub.)")
    pdf.h2("Passo 3 — Criar ambiente virtual e instalar dependencias")
    pdf.code(
        "python -m venv venv\n"
        "venv\\Scripts\\activate\n"
        "pip install -r requirements.txt\n"
        "playwright install chromium"
    )
    pdf.h2("Passo 4 — Colocar o .env")
    pdf.bullet("Copie o arquivo .env do notebook antigo para a raiz do projeto.")
    pdf.bullet("Minimo essencial:")
    pdf.code(
        "DATABASE_URL=postgresql://...\n"
        "DASHBOARD_PASS=sua_senha_patrao\n"
        "FLASK_SECRET_KEY=...  (opcional se derivar do banco)"
    )
    pdf.h2("Passo 5 — Testar o cockpit")
    pdf.code("venv\\Scripts\\python.exe cockpit\\start.py")
    pdf.body("Ou duplo clique em Abrir-Cockpit.bat. Abre http://127.0.0.1:5055")
    pdf.h2("Passo 6 — Rodar uma missao de teste")
    pdf.bullet("No cockpit: escolha usuario (ou Livre), nicho, cidade, meta baixa (ex: 5).")
    pdf.bullet("Adicionar na fila -> Rodar fila.")
    pdf.bullet("Veja o log ao vivo e o contador de leads.")
    pdf.bullet("No site (patrao): Status do Robo e Leads / sobras.")

    # 5 COCKPIT VS SITE
    pdf.add_page()
    pdf.h1("5. O que e o que")
    pdf.h3("Site do cliente (Vercel)")
    pdf.bullet("URL publica do projeto.")
    pdf.bullet("Login do cliente / patrao.")
    pdf.bullet("Ver leads, WhatsApp, status, usuarios.")
    pdf.ln(1)
    pdf.h3("Cockpit (local)")
    pdf.bullet("So no SEU PC/notebook.")
    pdf.bullet("Fila de missoes, Run, Parar, so Score.")
    pdf.bullet("NAO e o produto que o cliente usa.")
    pdf.ln(1)
    pdf.h3("Comando classico (ainda existe)")
    pdf.code("venv\\Scripts\\activate\npython main.py run")
    pdf.body("Equivalente ao 'Rodar' do cockpit, mas pelo terminal.")

    # 6 MULTI CIDADE
    pdf.h1("6. Cliente em outra cidade")
    pdf.body(
        "Nao precisa instalar nada no PC do cliente. Voce cria a conta no painel Usuarios, "
        "define cota e 'Recebe leads', roda a missao no SEU notebook com o nicho/cidade do cliente, "
        "e o lead cai no login dele no site."
    )
    pdf.bullet("Cliente: so navegador + login.")
    pdf.bullet("Voce: bot + cockpit no notebook.")
    pdf.bullet("Se o cliente pedir 'so o site', nao manda o repositorio nem o .env.")

    # 7 SEGURANÇA
    pdf.h1("7. Seguranca (nao pule)")
    pdf.bullet("Nunca commitar .env no Git.")
    pdf.bullet("Nao rodar dois bots ao mesmo tempo no mesmo banco.")
    pdf.bullet("Nao compartilhar login patrao com cliente.")
    pdf.bullet("Cockpit por padrao e so localhost (127.0.0.1) — nao expoe na internet aberta.")
    pdf.bullet("Se um dia abrir o cockpit na rede, coloque senha e use VPN (ex: Tailscale).")

    # 8 CHECKLIST
    pdf.h1("8. Checklist rapido (notebook novo)")
    for i, t in enumerate(
        [
            "Git instalado",
            "Python instalado (PATH ok)",
            "git clone do repositorio",
            "venv + pip install -r requirements.txt",
            "playwright install chromium",
            ".env copiado e DATABASE_URL testada",
            "cockpit\\start.py abre sem erro",
            "missao de teste com meta baixa ok",
            "lead aparece no site (sobras ou usuario)",
        ],
        start=1,
    ):
        pdf.bullet(f"[{i}] {t}")

    # 9 PROBLEMAS
    pdf.add_page()
    pdf.h1("9. Problemas comuns")
    pdf.h3("Bot rodou imobiliaria em vez de barbearia")
    pdf.body("O plano salvo no banco ainda tinha o nicho antigo. No cockpit, crie missao NOVA so com o nicho certo e rode de novo.")
    pdf.h3("POST /api/run 409")
    pdf.body("Ja existe bot rodando (CMD ou missao anterior). Clique Parar, feche main.py run antigo, tente de novo.")
    pdf.h3("Abrir-Cockpit.bat quebra com erro 'cp' / 'M'")
    pdf.body("Arquivo .bat corrompido ou LF. Use: venv\\Scripts\\python.exe cockpit\\start.py")
    pdf.h3("Cliente nao ve leads")
    pdf.body("Lead esta em sobras (assigned_to vazio) ou conta com cota 0 / filtro. Patrao distribui ou missao com dono forçado.")
    pdf.h3("Playwright / Chromium falha")
    pdf.code("venv\\Scripts\\activate\nplaywright install chromium")

    # 10 RESUMO
    pdf.h1("10. Resumo em 30 segundos")
    pdf.body(
        "1) Site = clientes em qualquer cidade.\n"
        "2) Bot + Cockpit = seu notebook (ou um so PC fixo).\n"
        "3) Outro notebook seu = git clone + venv + .env + playwright.\n"
        "4) Nao rode dois bots juntos.\n"
        "5) Cliente final so precisa do login no site — nao do codigo."
    )
    pdf.ln(4)
    pdf.box(
        "Proximo nivel (quando tiver varios pagantes)",
        "Subir o bot num VPS 24h + Tailscale pro cockpit no celular.\n"
        "Ate la, 1 notebook com o projeto e o .env resolve 95% dos casos.",
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(OUT))
    return OUT


if __name__ == "__main__":
    path = build()
    print(path)

# ProspecTHOR — Próximos passos

Atualizado: 2026-07-21  
Restrição: **sem API paga** (orçamento zero).

---

## Resumo da fila

| # | Frente | Status |
|---|--------|--------|
| **1** | Busca magra + só conta se tem **WA/tel ou Instagram** + parar cedo + cache site | **FEITO** |
| **2** | Fonte B (OSM + CNPJ BrasilAPI) em **paralelo** ao Maps no cockpit | **FEITO** |
| **3** | Painel: badge + CTA **WA → Instagram** | **FEITO** |
| **4** | Cockpit: fila auto + reordenar / selecionar+rodar / ✕ / editar | **FEITO** |
| **5** | Patrão: distribuir sobra escolhendo + ✕ apagar lead | **FEITO** |
| **6** | **Validar WhatsApp de verdade** (próximo) | **Próximo** |
| — | Nuvem / API paga | Adiado |

---

## Próximo: 6) Validar WhatsApp real

Hoje: telefone “parece celular” → tratamos como WA.  
Falta: saber se o número **tem WhatsApp ativo**.

Ideias (sem furar orçamento no começo):
1. Filtro celular BR + campo `whatsapp_status` (`desconhecido | provavel | confirmado | sem_whatsapp`)
2. Botão do vendedor: “WA ok” / “não tem WA”
3. Se sem WA e tem IG → CTA Instagram (já feito na 3)
4. Checker automático só se achar caminho estável **sem** API cara

---

## O que entrou nas frentes 1–3

### 1 — Maps magro + meta honesta
- Early-skip site + **cache** `data/known_has_site.json`
- **Só conta lead** com telefone **ou** Instagram
- Parar cedo no bairro (`EARLY_STOP_INSPECT=40`, `EARLY_STOP_MIN_LEADS=2`)
- Queries um pouco mais magras (termo curto primeiro)
- IG do Maps (website instagram.com) vira `instagram_url`

### 2 — Fonte B paralela
- `python main.py fonte-b` ou `cnpj`
- Cockpit sobe **Maps + Fonte B** juntos (`COCKPIT_PARALLEL_FONTEB=true`)
- OSM/Overpass + enriquecimento BrasilAPI se tiver tag CNPJ
- **Mesma regra**: sem tel/IG → não grava

### 3 — Painel
- Badge **WA / IG / Tel / —**
- Botão principal: WhatsApp se tiver número; senão Instagram
- Prompt modal escolhe canal conforme o lead

### 4 e 5 — já estavam feitos

---

## Backlog depois

- OpenStreetMap tags por mais nichos  
- 2º notebook / turnos  
- Nuvem paga  
- Validação WA real (frente 6)

"""
notifier.py - Módulo de notificações e alertas

Envia notificações quando novos leads são encontrados ou quando
ciclos de prospecção são concluídos.

Canais de notificação suportados:

    TELEGRAM (primário):
        - Envia mensagem formatada com os dados do lead
        - Suporte a formatação Markdown (negrito, itálico, links)
        - Botões inline para ação rápida (ex: "Ver no Maps", "Ligar")
        - Notificação de resumo ao final de cada ciclo de prospecção
        - Configurado via TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env

    EMAIL (secundário):
        - Envia email HTML formatado com lista de leads do dia
        - Configurado via SMTP (Gmail, Outlook, ou servidor próprio)
        - Suporte a anexo do arquivo CSV/Excel exportado
        - Configurado via variáveis EMAIL_* no .env

    WEBHOOK (genérico):
        - Envia payload JSON para qualquer URL de webhook
        - Útil para integrar com Slack, Discord, Zapier, Make, etc.
        - Configurado via WEBHOOK_URL no .env

Tipos de notificações:

    LEAD ENCONTRADO:
        Disparado quando um novo lead de alta/média prioridade é persistido.
        Conteúdo: nome da empresa, cidade, nicho, pontuação, pontos de contato.

    RESUMO DIÁRIO:
        Disparado ao final de um ciclo completo de prospecção.
        Conteúdo: total de empresas verificadas, leads encontrados por prioridade,
                  tempo de execução, erros encontrados.

    ALERTA DE ERRO:
        Disparado quando ocorre falha crítica na execução.
        Conteúdo: tipo de erro, módulo afetado, stack trace resumido.

    LEAD DE OURO:
        Notificação especial (com som/prioridade alta) para leads com pontuação
        máxima (71+ pontos).

Funções principais:
    notify_new_lead(lead: dict, channel: str = "telegram") -> bool
        Envia notificação de novo lead para o canal especificado.

    send_daily_summary(stats: dict) -> bool
        Envia resumo do ciclo de prospecção.

    send_error_alert(error: Exception, context: str) -> bool
        Envia alerta de erro crítico.

    send_telegram(message: str, chat_id: str | None = None) -> bool
        Envia mensagem diretamente para o Telegram.

    send_email(subject: str, body: str, attachment: str | None = None) -> bool
        Envia email via SMTP configurado.

Dependências:
    - requests          : Para chamadas à API do Telegram e webhooks
    - smtplib           : Biblioteca padrão para envio de emails (stdlib)
    - email             : Para construir emails HTML (stdlib)
    - python-dotenv     : Para carregar configurações do .env
"""

# TODO: Implementar send_telegram() com suporte a Markdown e botões inline
# TODO: Implementar notify_new_lead() com formatação rica da mensagem
# TODO: Implementar send_daily_summary() com estatísticas do ciclo
# TODO: Implementar send_error_alert() para monitoramento de falhas
# TODO: Implementar send_email() com template HTML para o resumo diário
# TODO: Implementar send_webhook() para integrações genéricas
# TODO: Implementar fila de notificações para evitar rate limiting do Telegram
# TODO: Implementar configuração de canais via settings.py (ativar/desativar)

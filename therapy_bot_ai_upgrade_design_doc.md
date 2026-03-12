# Design Document — Upgrade do Bot de Captação para IA

## 1. Objetivo
Adicionar uma camada de IA ao bot atual de captação de leads para tornar a conversa mais natural, melhorar a verificação do lead, classificar intenção e qualidade do contato, e entregar dados estruturados para follow-up humano sem transformar o bot em atendimento clínico.

## 2. Resultado esperado
O sistema deve:
- responder de forma mais humana e contextual
- coletar dados essenciais do lead sem parecer formulário
- classificar o lead automaticamente
- detectar urgência e encaminhar para protocolo seguro
- registrar tudo em formato estruturado no CRM/Sheets
- permitir revisão humana e melhoria contínua

## 3. Escopo
### Incluído
- integração do bot com um modelo de IA
- orquestração entre fluxo guiado e respostas livres
- classificação automática de lead
- extração de dados estruturados
- guardrails de segurança
- painel/logs para observabilidade
- integração com WhatsApp/site chat/CRM/Google Sheets

### Fora do escopo inicial
- diagnóstico clínico
- aconselhamento terapêutico profundo
- voz em tempo real
- automação total sem supervisão humana

## 4. Princípios do produto
- parecer humano, mas nunca se passar por terapeuta
- conduzir a conversa para coleta de contexto útil
- reduzir abandono
- identificar urgência rapidamente
- encaminhar casos válidos para follow-up humano
- manter compliance e privacidade

## 5. Casos de uso principais
1. Lead chega pelo site ou WhatsApp.
2. Bot acolhe, entende motivo do contato e coleta contexto.
3. IA adapta a resposta ao que a pessoa escreveu.
4. Sistema extrai atributos do lead.
5. Lead recebe classificação.
6. Caso seja um lead válido, sistema oferece próximo passo.
7. Caso haja sinal de crise, sai do fluxo comercial e entra no fluxo de segurança.

## 6. Arquitetura proposta
### Componentes
1. **Canal de entrada**
   - WhatsApp Cloud API
   - widget do site
2. **Webhook de entrada**
   - recebe mensagens
   - valida assinatura
   - normaliza payload
3. **Conversation Orchestrator**
   - recupera estado da conversa
   - decide qual módulo chamar
4. **LLM Service**
   - gera resposta natural
   - faz classificação e extração estruturada
5. **Rules Engine**
   - aplica regras de negócio
   - força perguntas obrigatórias
   - detecta quando escalar para humano
6. **Safety Layer**
   - moderação
   - detecção de risco/autoagressão/crise
   - resposta segura e handoff
7. **Lead Store / CRM Adapter**
   - salva lead, score, tags e transcript
   - envia para Google Sheets, Airtable, HubSpot ou CRM próprio
8. **Analytics & Observability**
   - logs
   - métricas
   - alertas
9. **Admin Config**
   - prompts
   - mensagens fixas
   - critérios de score

## 7. Fluxo técnico de alto nível
1. Usuário envia mensagem.
2. Canal entrega evento ao webhook.
3. Webhook valida e publica no orquestrador.
4. Orquestrador busca histórico e estado.
5. Safety Layer roda primeiro.
6. Se houver risco, ativa fluxo de crise.
7. Se não houver risco, chama LLM + regras.
8. LLM retorna:
   - resposta ao usuário
   - dados extraídos
   - classificação
   - confiança
   - intenção do próximo passo
9. Rules Engine valida se faltam campos obrigatórios.
10. Sistema persiste transcript e atributos.
11. Resposta é enviada ao canal.
12. Se lead estiver pronto, dispara follow-up humano.

## 8. Estratégia de conversa
### Modelo híbrido
Usar **IA + fluxo guiado**.

Não deixar a IA conduzir tudo sozinha. O ideal é:
- perguntas críticas obrigatórias controladas por regras
- linguagem e transições geradas pela IA
- extração estruturada em cada turno

Isso reduz respostas aleatórias e melhora consistência.

### Informações mínimas para coleta
- nome
- sexo
- idade
- para quem é o atendimento (para ela mesma, parente ou filho)
- qual tema gostaria de tratar nas sessões
- urgência percebida

### Regras obrigatórias de oferta social
- informar que o **atendimento social acontece somente no período da tarde**
- informar o **valor social de R$ 60,00** no momento adequado da qualificação
- registrar se a pessoa aceitou seguir com a modalidade social

### Campos opcionais úteis
Esses campos devem ser perguntados **somente após a finalização da classificação do lead** e **são opcionais**. Eles não devem bloquear o fluxo principal nem impedir o encaminhamento para atendimento.

Campos sugeridos:
- como encontrou o serviço
- já fez terapia antes
- disponibilidade financeira aproximada
- preferência por terapeuta mulher/homem

## 9. Classificação de lead
### Eixos de classificação
1. **Intenção**
   - curiosidade
   - buscando marcar
   - buscando informações
   - crise/suporte urgente
2. **Fit de serviço**
   - bom fit
   - fit parcial
   - baixo fit
3. **Prontidão**
   - frio
   - morno
   - quente
4. **Qualidade do contato**
   - válido
   - incompleto
   - provável spam
5. **Canal**
   - site
   - orgânico
   - anúncio
   - indicação

### Exemplo de score
- demonstrou dor clara: +20
- respondeu contato e nome: +15
- pediu agenda/valor: +20
- disponibilidade informada: +10
- resposta vaga ou troll: -20
- sinais de urgência clínica: não pontua comercialmente, desvia para segurança

## 10. Fluxo de segurança
O bot não deve agir como terapeuta nem manejar crise de forma improvisada.

### Detectar sinais como
- ideação suicida
- automutilação
- violência iminente
- surto / desorganização grave
- pedido explícito de ajuda urgente

### Ação
- interromper o fluxo comercial
- responder com acolhimento breve
- informar que o bot não substitui suporte emergencial
- oferecer contato com emergência local / CVV / pronto atendimento
- sinalizar para revisão humana imediata
- marcar transcript com prioridade alta

## 11. Modelo de dados
### Tabela `conversations`
- conversation_id
- channel
- user_id
- started_at
- updated_at
- status
- current_stage
- assigned_human

### Tabela `messages`
- message_id
- conversation_id
- role
- raw_text
- normalized_text
- llm_prompt_version
- llm_response_version
- timestamp
- safety_flag

### Tabela `leads`
- lead_id
- conversation_id
- name
- contact_phone
- contact_email
- city
- state
- age_group
- service_interest
- therapy_mode_preference
- schedule_preference
- intent
- readiness_score
- fit_score
- verification_status
- risk_level
- source
- notes_summary
- extracted_json
- created_at
- updated_at

## 12. Estado da conversa
### Exemplo de estados
- new
- greeting
- discovering_need
- collecting_profile
- qualifying
- collecting_contact
- scheduling_interest
- handoff_ready
- safety_protocol
- closed

### Regras
- o estado vem das regras, não só da IA
- a IA sugere, o orquestrador confirma
- timeout de sessão configurável

## 13. Integração com LLM
### Recomendação de abordagem
Usar a **Responses API** para novas integrações. A OpenAI está direcionando novos projetos para ela e a Assistants API está marcada para descontinuação em 26 de agosto de 2026. ([developers.openai.com](https://developers.openai.com/api/docs/guides/migrate-to-responses/?utm_source=chatgpt.com))

### Padrão de chamada
Em cada turno, enviar:
- mensagem atual do usuário
- resumo curto da conversa
- estado atual
- campos já coletados
- políticas de segurança
- objetivo do turno

### Saída esperada do modelo
A resposta do modelo deve ser **estruturada** e conter:
- `reply_text`
- `intent`
- `next_best_question`
- `lead_fields_extracted`
- `lead_quality`
- `risk_level`
- `handoff_recommended`
- `confidence`

### Exemplo de contrato JSON
```json
{
  "reply_text": "string",
  "intent": "info|book|unclear|crisis|spam",
  "lead_fields_extracted": {
    "name": null,
    "age_group": null,
    "city": null,
    "contact_phone": null,
    "service_interest": null,
    "schedule_preference": null
  },
  "lead_quality": "cold|warm|hot|invalid",
  "risk_level": "none|low|medium|high",
  "handoff_recommended": false,
  "next_required_field": "contact_phone",
  "confidence": 0.91
}
```

## 14. Estratégia de prompting
### Prompt base do sistema
O prompt deve instruir o modelo a:
- falar em PT-BR natural
- soar acolhedor e humano
- não inventar informações
- não diagnosticar
- não dar conselho clínico profundo
- priorizar coleta gradual de dados
- sempre produzir JSON válido
- ativar protocolo de segurança em sinais de risco

### Técnicas importantes
- usar few-shot com exemplos bons e ruins
- manter resumo da conversa em vez de histórico inteiro
- separar prompt de persona, regras, contexto e output schema
- versionar prompts

## 15. Ferramentas e integrações
### Entrada e saída
- **WhatsApp Cloud API** para envio/recebimento, mensagens interativas e tracking de status. ([developers.facebook.com](https://developers.facebook.com/documentation/business-messaging/whatsapp/reference/whatsapp-business-phone-number/message-api?utm_source=chatgpt.com))
- Considerar desde já compatibilidade com mudanças recentes como BSUIDs nos webhooks do WhatsApp. ([developers.facebook.com](https://developers.facebook.com/documentation/business-messaging/whatsapp/business-scoped-user-ids/?utm_source=chatgpt.com))

### IA
- OpenAI Responses API com structured outputs / function calling.
- Function calling é útil para salvar lead, atualizar CRM, disparar handoff e registrar tags. A OpenAI mantém a orientação de usar tool/function calling no fluxo de Responses. ([developers.openai.com](https://developers.openai.com/api/docs/guides/migrate-to-responses/?utm_source=chatgpt.com))

### Persistência
- PostgreSQL para produção
- Redis para sessão/cache/rate limiting
- S3 compatível para transcripts exportados, se necessário

### CRM / planilha
- Google Sheets API para operação simples
- HubSpot/Pipedrive/Airtable para operação madura

## 16. Regras de negócio sugeridas
- não pedir tudo de uma vez
- no máximo 1 pergunta principal por mensagem
- após 2 mensagens do usuário sem contato, tentar capturar contato
- se a pessoa pedir preço logo no início, responder e continuar qualificação
- se a pessoa ficar vaga por 2 turnos, usar pergunta fechada
- se detectar spam, encerrar educadamente
- se lead quente, oferecer handoff humano rápido
- quando o lead entrar no fluxo de atendimento social, informar claramente:
  - que o atendimento social é **somente no período da tarde**
  - que o **valor social é R$ 60,00**
- registrar no lead se houve interesse após a informação de período e valor

### Ordem recomendada de qualificação
1. acolhimento inicial
2. identificar para quem é o atendimento
3. coletar nome
4. coletar sexo
5. coletar idade
6. entender o tema principal a ser tratado
7. identificar urgência
8. informar regras da categoria social (somente tarde)
9. informar valor social (R$ 60,00)
10. confirmar interesse em seguir
11. capturar contato para follow-up, se ainda não tiver sido capturado

### Exemplo de bloco estruturado mínimo
```json
{
  "name": "string",
  "gender": "feminino|masculino|outro|nao_informado",
  "age": 0,
  "service_for": "self|relative|child",
  "session_topic": "string",
  "urgency": "low|medium|high",
  "social_period_ack": true,
  "social_price_ack": true,
  "social_interest": true
}
```

## 17. Handoff para humano
### Condições para handoff
- lead quente
- pediu agendamento
- pediu falar com pessoa real
- score acima do threshold
- caso sensível
- baixa confiança da IA em continuar

### Payload do handoff
- resumo da conversa
- nome
- contato
- motivo da busca
- score
- tags
- risco
- última mensagem do usuário
- sugestão de próxima abordagem

## 18. Observabilidade
### Métricas principais
- taxa de resposta
- taxa de abandono por etapa
- taxa de captura de contato
- taxa de lead válido
- taxa de handoff
- taxa de crise detectada
- tempo médio por conversa
- custo por conversa
- latência por turno

### Logs essenciais
- input do usuário
- output da IA
- campos extraídos
- versão do prompt
- erro de validação
- fallback acionado

## 19. Segurança, privacidade e compliance
- criptografar dados sensíveis em repouso
- usar TLS em trânsito
- mascarar PII nos logs
- definir retenção de transcripts
- permitir exclusão de dados
- registrar consentimento quando necessário
- deixar claro que o contato inicial não substitui atendimento profissional ou emergência

## 20. Anti-falhas e fallback
### Quando a IA falhar
- usar resposta de fallback fixa
- preservar o estado da conversa
- continuar com fluxo guiado
- marcar evento para revisão

### Fallback example
> Obrigado por me contar isso. Posso te fazer uma pergunta rápida para te direcionar melhor? Você está buscando atendimento para você ou para outra pessoa?

## 21. Estratégia de implementação por fases
### Fase 1 — Base técnica
- webhook
- banco
- estado da conversa
- integração com canal
- logs básicos

### Fase 2 — IA controlada
- prompt base
- structured output
- extração de campos
- classificação inicial

### Fase 3 — Safety + handoff
- detecção de risco
- regras de handoff
- integração com CRM/Sheets
- alertas

### Fase 4 — Otimização
- A/B de prompts
- score refinado
- analytics
- relatórios de conversão

## 22. Stack sugerida
### Backend
- Node.js com TypeScript
- Fastify ou NestJS
- Zod para validação
- Prisma ou Drizzle ORM

### Infra
- Postgres
- Redis
- queue com BullMQ
- deploy em Railway, Render, Fly.io, VPS ou container próprio

### Front/admin
- Next.js simples para painel interno

## 23. Endpoints sugeridos
- `POST /webhooks/whatsapp`
- `POST /chat/reply`
- `POST /leads`
- `PATCH /conversations/:id/state`
- `POST /handoff`
- `GET /health`
- `GET /metrics`

## 24. Exemplo de módulos de código
- `channel-adapters/whatsapp.ts`
- `orchestrator/conversation-manager.ts`
- `orchestrator/state-machine.ts`
- `llm/generate-reply.ts`
- `llm/extract-lead-fields.ts`
- `safety/risk-detector.ts`
- `crm/push-lead.ts`
- `analytics/track-event.ts`
- `config/prompt-registry.ts`

## 25. Testes necessários
### Unitários
- parsing do webhook
- state transitions
- score rules
- JSON validation

### Integração
- canal → webhook → IA → persistência → resposta
- CRM handoff
- fallback quando API da IA falha

### Segurança
- prompt injection básico
- bypass de guardrails
- inputs maliciosos
- rate limiting

### Conversa
- lead quente
- lead frio
- spam
- pedido de preço
- crise
- contato incompleto

## 26. Riscos do projeto
- IA responder demais e parar de qualificar
- conversa parecer clínica demais
- capturar poucos dados úteis
- falso positivo em crise
- custo subir com histórico longo
- dependência excessiva de prompt

## 27. Mitigações
- modelo híbrido IA + regras
- resumo de contexto
- output schema rígido
- thresholds de confiança
- handoff humano rápido
- dashboards para revisão semanal

## 28. Custos e decisões de plataforma
A OpenAI informa que a Responses API não tem preço separado; o custo acompanha o modelo usado. A página oficial também lista custos específicos para ferramentas, como file search e web search. ([openai.com](https://openai.com/api/pricing/?utm_source=chatgpt.com))

Para esse caso, a arquitetura mais segura é:
- modelo principal mais barato para conversa e classificação comum
- modelo mais forte só para casos ambíguos, longos ou sensíveis
- resumo de contexto para reduzir tokens
- retries limitados

## 29. Recomendação prática de modelo operacional
- **Fluxo normal:** modelo equilibrado, rápido e barato
- **Casos ambíguos:** escalonamento para modelo mais forte
- **Crise/sensível:** não depender só de modelo; sempre aplicar regras determinísticas + revisão humana

## 30. Critérios de sucesso
O upgrade é bem-sucedido quando:
- aumenta a taxa de captura de contato
- melhora a taxa de lead válido
- reduz abandono
- mantém custo por lead saudável
- reduz necessidade de intervenção manual precoce
- classifica urgência com baixo erro operacional

## 31. Próximos passos de engenharia
1. Mapear o fluxo atual do bot.
2. Definir campos obrigatórios do lead.
3. Definir thresholds de score.
4. Implementar banco e state machine.
5. Subir integração com LLM via Responses API.
6. Implementar structured output.
7. Conectar CRM/Sheets.
8. Implementar safety layer.
9. Testar com transcripts reais anonimizados.
10. Medir e iterar.

## 32. Recomendação final
Não trate esse upgrade como “bot com IA”. Trate como um **sistema de qualificação assistida por IA**, com:
- conversa natural
- regras rígidas
- saída estruturada
- handoff humano
- segurança forte

Essa arquitetura tende a gerar melhor conversão, melhor qualidade de lead e menos comportamento imprevisível do que um chatbot puramente generativo.


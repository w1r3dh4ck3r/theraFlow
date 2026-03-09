
# WhatsApp Lead Qualification Bot
## Psicanálise Practice – Technical Design Document (Conversion-Optimized Version)

---

# 1. Objective

Design and implement a **custom WhatsApp lead qualification bot** that:

1. Receives visitors from the website.
2. Starts a guided conversation automatically.
3. Qualifies potential clients with a short intake flow.
4. Stores answers in **Google Sheets**.
5. Notifies the business for **fast human follow‑up**.
6. Maximizes **conversion into first appointments**.

This system is optimized specifically for **therapy / psicanálise businesses**, where trust, safety, and emotional tone are critical.

---

# 2. High Conversion Funnel Strategy

The highest converting flow used by clinics and service businesses is:

Website visitor  
↓  
Clicks WhatsApp  
↓  
Bot greets and qualifies lead  
↓  
Lead saved to Google Sheets / CRM  
↓  
Assistant receives notification  
↓  
Human response within 5–10 minutes  
↓  
Offer first appointment  
↓  
Optional scheduling link

Important rule:

Fast human follow-up is the **biggest conversion factor**.

Response time effect:

| Response Time | Conversion Impact |
|---|---|
Under 5 minutes | Highest |
5–30 minutes | Good |
1–2 hours | Moderate |
Next day | Very low |

The bot exists primarily to:

• remove response delay  
• collect structured lead information  
• make human follow‑up easier  

---

# 3. Why WhatsApp Instead of Forms

Psychological advantage:

People prefer **conversation over forms**, especially in emotional contexts like therapy.

Advantages:

• lower friction  
• instant response  
• higher trust  
• higher completion rate  
• higher appointment rate

Typical clinic funnel:

Landing Page → WhatsApp Conversation → Appointment

---

# 4. System Architecture

Core components:

Website  
↓  
WhatsApp Click‑to‑Chat Link  
↓  
WhatsApp Cloud API  
↓  
Webhook Server (Backend)  
↓  
Conversation State Machine  
↓  
Database (Lead + Conversation State)  
↓  
Google Sheets Sync  
↓  
Human Follow‑up

---

# 5. WhatsApp Cloud API Integration

This is the most technically complex part.

Meta provides the **WhatsApp Business Platform Cloud API**.

Key concepts:

Business Manager  
WhatsApp Business Account (WABA)  
Phone Number ID  
Access Token  
Webhook Endpoint

Official documentation:
https://developers.facebook.com/docs/whatsapp

---

# 6. Meta API Connection Flow

## Step 1 — Create Meta App

Create a Meta Developer app.

Enable product:

WhatsApp

Inside the app dashboard you will get:

Temporary Access Token  
Phone Number ID  
WhatsApp Business Account ID

---

## Step 2 — Register Business Phone Number

Requirements:

• phone number not already used in WhatsApp  
• verified business manager

Once registered, the number becomes available through the API.

---

## Step 3 — Configure Webhook

Your backend must expose:

POST /webhook/whatsapp

Meta will send events here.

Verification step:

GET /webhook/whatsapp

Your server must return the **challenge parameter** to confirm ownership.

---

## Step 4 — Subscribe to Events

Subscribe to:

messages  
message_status  
message_template_status

These events allow you to receive:

• user messages  
• button clicks  
• delivery confirmations

---

## Step 5 — Sending Messages

Messages are sent using the endpoint:

POST /PHONE_NUMBER_ID/messages

Authorization:

Bearer Access Token

Message types supported:

Text  
Reply Buttons  
List Messages  
Templates

For this bot we primarily use:

• text messages
• reply buttons

---

# 7. Conversation Design Principles

The bot must feel:

• human  
• calm  
• welcoming  
• not robotic

Bad example:

"SELECT OPTION 1"

Good example:

"Olá, fico feliz que você tenha entrado em contato."

The tone should lower psychological resistance.

---

# 8. Bot Conversation Flow

Step 1 — Greeting

Message:

Olá, eu sou a assistente virtual da Karoline.

Posso te fazer algumas perguntas rápidas para entender como podemos ajudar?

Buttons:

Sim  
Prefiro falar com uma pessoa

---

Step 2 — Who is the therapy for?

Para quem seria o atendimento?

Buttons:

Para mim  
Para meu filho(a)  
Para meu parceiro(a)  
Outro familiar

---

Step 3 — Gender

Qual opção melhor representa a pessoa interessada no atendimento?

Buttons:

Mulher  
Homem  
Prefere não informar

---

Step 4 — Age group

Qual a faixa etária?

Buttons:

Até 12  
13–17  
18–24  
25–34  
35–44  
45–59  
60+

---

Step 5 — Location

Em qual cidade você está?

Free text.

---

Step 6 — Format

Você prefere atendimento:

Online  
Presencial  
Tanto faz

---

Step 7 — First therapy

Seria sua primeira experiência em terapia?

Sim  
Não

---

Step 8 — Main topic

Qual tema mais se aproxima do que você gostaria de trabalhar?

Ansiedade  
Relacionamentos  
Autoestima  
Luto  
Família  
Outro

---

Step 9 — Urgency

Você gostaria de começar:

O quanto antes  
Nesta semana  
Neste mês  
Ainda estou pensando

---

Step 10 — Preferred time

Qual período é melhor para você?

Manhã  
Tarde  
Noite  
Flexível

---

Step 11 — Appointment intent

Você gostaria de agendar uma primeira conversa?

Sim  
Quero tirar dúvidas primeiro

---

Step 12 — Optional note

Se quiser, pode escrever em uma frase o que te motivou a procurar atendimento agora.

Ou pode pular.

---

Step 13 — Consent

Vamos registrar essas informações apenas para facilitar o primeiro contato.

Você concorda em continuar?

Sim  
Não

---

Step 14 — Closing

Perfeito, já organizei suas informações.

A Karoline costuma responder novos contatos ainda hoje.

Se preferir, você também pode ver horários disponíveis:

[link de agendamento]

---

# 9. Lead Storage (Google Sheets)

Create a spreadsheet with columns:

lead_id  
timestamp  
whatsapp_name  
phone_number  
gender  
age_group  
city  
format  
first_therapy  
topic  
urgency  
preferred_time  
appointment_interest  
note  
status

Recommended tabs:

Leads  
Analytics  
Config

---

# 10. Lead Scoring

Assign score based on intent.

Example:

Appointment requested → +3  
Urgency high → +2  
Left note → +1  

Lead priority:

0‑2 → Low  
3‑5 → Warm  
6+ → Hot

---

# 11. Human Follow‑Up Strategy

After qualification:

Assistant should message within 10 minutes.

Message example:

Olá, aqui é a Karoline.

Vi que você entrou em contato pelo site.

Gostaria de te explicar como funciona o atendimento e ver um horário que seja confortável para você.

---

# 12. Optional Call Strategy

Only call if the lead agrees.

Bot question:

Podemos te ligar rapidamente para explicar como funciona?

Sim  
Prefiro WhatsApp

---

# 13. Data Privacy

Follow LGPD principles.

Avoid collecting:

• diagnosis
• medication
• detailed trauma history

Collect only basic information for first contact.

---

# 14. Implementation Phases

Phase 1 — MVP

• WhatsApp Cloud API connection  
• webhook server  
• scripted conversation  
• Google Sheets logging  
• manual follow‑up  

---

Phase 2 — Optimization

• notifications to assistant  
• appointment link integration  
• analytics dashboard  

---

Phase 3 — Advanced

• AI summary of conversations  
• CRM integration  
• follow‑up automation

---

# 15. Key Success Factors

The bot itself is not the main conversion driver.

The most important factors are:

1. Fast response time  
2. Human tone  
3. Simple questions  
4. Easy scheduling  
5. Emotional safety

---

End of document.

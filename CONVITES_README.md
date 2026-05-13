# 🎯 Sistema de Convites por Email - FSBuilder

## ✅ Implementação Concluída

O sistema de convites por email foi implementado com sucesso! Agora o FSBuilder possui um fluxo moderno e profissional de onboarding de usuários.

---

## 📋 O Que Foi Implementado

### 1. **Infraestrutura**
- ✅ Celery configurado para tasks assíncronas
- ✅ Redis como broker de mensagens
- ✅ django-celery-beat para tasks periódicas
- ✅ Email backend configurável (console/SMTP/SES)
- ✅ Flower para monitoramento (opcional)

### 2. **Backend**
- ✅ Model `Convite` com todos os campos necessários
- ✅ Service layer (`convite_service.py`) com lógica de negócio
- ✅ Celery tasks para envio assíncrono de emails
- ✅ Task periódica para expirar convites antigos
- ✅ Sistema completo de permissões e validações

### 3. **Frontend**
- ✅ Form de criação de convite (email + role)
- ✅ Lista de convites com filtros e estatísticas
- ✅ Página pública de aceite de convite (signup)
- ✅ Templates de email HTML profissionais
- ✅ Templates plain text (fallback)

### 4. **Funcionalidades**
- ✅ Criar convite (MASTER/ADMIN)
- ✅ Listar convites (com filtros por status)
- ✅ Reenviar convite (gera novo token)
- ✅ Cancelar convite
- ✅ Aceitar convite (signup público)
- ✅ Auto-login após cadastro
- ✅ Empresa ativa pré-selecionada

---

## 🚀 Como Usar

### **Passo 1: Instalar Dependências**

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Novas dependências instaladas:
- `celery==5.3.4`
- `redis==5.0.1`
- `django-celery-beat==2.5.0`
- `flower==2.0.1`

---

### **Passo 2: Configurar Variáveis de Ambiente**

Atualize seu arquivo `.env` com as novas configurações:

```env
# Email Backend (desenvolvimento)
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend

# Para produção, use SMTP ou AWS SES:
# EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
# EMAIL_HOST=smtp.gmail.com
# EMAIL_PORT=587
# EMAIL_USE_TLS=True
# EMAIL_HOST_USER=seu-email@gmail.com
# EMAIL_HOST_PASSWORD=sua-senha-ou-app-password
DEFAULT_FROM_EMAIL=noreply@fsbuilder.com

# Redis (para Celery)
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# Convites
CONVITE_EXPIRACAO_DIAS=7
CONVITE_MAX_REENVIOS=3
CONVITE_ENVIO_SINCRONO=False
BASE_URL=http://localhost:8000
```

---

### **Passo 3: Instalar e Iniciar Redis**

**Windows:**
1. Baixe Redis para Windows: https://github.com/tporadowski/redis/releases
2. Extraia e execute `redis-server.exe`

**Ou use Docker:**
```powershell
docker run -d -p 6379:6379 redis:alpine
```

---

### **Passo 4: Aplicar Migrações**

```powershell
.\venv\Scripts\python.exe manage.py migrate
```

Isso criará:
- Tabela `usuarios_convite`
- Tabelas do `django_celery_beat`

---

### **Passo 5: Iniciar Celery Worker**

Em um **novo terminal**, execute:

```powershell
.\venv\Scripts\celery.exe -A cinnamon worker -l info --pool=solo
```

> **Nota:** `--pool=solo` é necessário no Windows. Em Linux/Mac, pode omitir.

---

### **Passo 6: Iniciar Celery Beat (Opcional)**

Para tasks periódicas (expirar convites automaticamente):

```powershell
.\venv\Scripts\celery.exe -A cinnamon beat -l info
```

---

### **Passo 7: Iniciar Servidor Django**

```powershell
.\venv\Scripts\python.exe manage.py runserver
```

---

## 📧 Fluxo de Uso

### **1. Admin Cria Convite**

1. Faça login como MASTER ou ADMIN
2. Acesse: **Empresa → Usuários**
3. Clique em **"Convidar Usuário"** (ou vá para `/empresa/usuarios/convidar/`)
4. Preencha:
   - Email do usuário
   - Papel (ADMIN, MEMBER, VIEWER)
5. Clique em **"Enviar Convite"**

**O que acontece:**
- Convite criado com status PENDING
- Email enviado assincronamente via Celery
- Token único gerado (UUID4)
- Expira em 7 dias

---

### **2. Usuário Recebe Email**

O usuário receberá um email profissional contendo:
- Nome da empresa
- Quem convidou
- Papel que terá
- **Botão "Aceitar Convite"** com link único
- Validade do convite

---

### **3. Usuário Aceita Convite**

1. Clica no link do email
2. É redirecionado para `/convites/aceitar/{token}/`
3. Vê página de signup com:
   - Email pré-preenchido (read-only)
   - Empresa e role exibidos
   - Form para: username, nome, senha
4. Preenche dados e clica **"Criar Minha Conta"**

**O que acontece:**
- Usuario criado no banco
- Membership criado (vincula usuário à empresa)
- Convite marcado como ACCEPTED
- Auto-login
- Empresa ativa já selecionada
- Redirecionado para dashboard

---

### **4. Admin Gerencia Convites**

Acesse: `/empresa/usuarios/convites/`

**Funcionalidades:**
- Visualizar todos os convites (com filtros)
- Ver estatísticas (total, pendentes, aceitos, expirados)
- **Reenviar** convites pendentes ou expirados
- **Cancelar** convites pendentes

---

## 🎨 URLs Disponíveis

| URL | Método | Acesso | Descrição |
|-----|--------|--------|-----------|
| `/empresa/usuarios/convidar/` | GET, POST | MASTER, ADMIN | Criar convite |
| `/empresa/usuarios/convites/` | GET | MASTER, ADMIN | Listar convites |
| `/empresa/usuarios/convites/{id}/reenviar/` | POST | MASTER, ADMIN | Reenviar convite |
| `/empresa/usuarios/convites/{id}/cancelar/` | POST | MASTER, ADMIN | Cancelar convite |
| `/convites/aceitar/{token}/` | GET, POST | **Público** | Aceitar convite (signup) |

---

## 🔧 Desenvolvimento vs. Produção

### **Modo Desenvolvimento**

Usa **console backend** → emails aparecem no terminal do Django:

```env
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
CONVITE_ENVIO_SINCRONO=True  # Opcional: envio síncrono (mais rápido em dev)
```

---

### **Modo Produção**

#### **Opção 1: SMTP (Gmail, Outlook, etc)**

```env
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=seu-email@gmail.com
EMAIL_HOST_PASSWORD=sua-senha-app
DEFAULT_FROM_EMAIL=noreply@fsbuilder.com
BASE_URL=https://fsbuilder.com
```

#### **Opção 2: AWS SES (Recomendado)**

```env
EMAIL_BACKEND=django_ses.SESBackend
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
AWS_SES_REGION_NAME=us-east-1
AWS_SES_REGION_ENDPOINT=email.us-east-1.amazonaws.com
DEFAULT_FROM_EMAIL=noreply@fsbuilder.com
```

---

## 📊 Monitoramento

### **Flower (Celery Monitoring)**

Execute:

```powershell
.\venv\Scripts\celery.exe -A cinnamon flower
```

Acesse: http://localhost:5555

Visualize:
- Tasks em execução
- Filas
- Workers ativos
- Histórico de tasks

---

## 🐛 Troubleshooting

### **Emails não estão sendo enviados**

1. Verifique se Celery worker está rodando
2. Verifique logs do Celery
3. Teste conexão SMTP manualmente:

```python
python manage.py shell

from django.core.mail import send_mail
send_mail('Teste', 'Mensagem teste', 'from@example.com', ['to@example.com'])
```

---

### **Redis não conecta**

```
ConnectionError: Error 10061 connecting to localhost:6379
```

**Solução:**
- Certifique-se que Redis está rodando
- Windows: execute `redis-server.exe`
- Docker: `docker run -d -p 6379:6379 redis:alpine`

---

### **Convites expirando automaticamente**

Certifique-se que **Celery Beat** está rodando:

```powershell
.\venv\Scripts\celery.exe -A cinnamon beat -l info
```

A task `expirar_convites_antigos` roda diariamente às 3h AM.

---

## 📝 Logs

Todos os eventos importantes são registrados:

```python
# Criação de convite
logger.info(f"Convite criado: {convite.id} | Email: {email}")

# Envio de email
logger.info(f"Email de convite enviado com sucesso: Convite {convite_id}")

# Aceite de convite
logger.info(f"Usuário criado via convite: {usuario.username}")
```

Configure logging em `settings.py` para persistir logs:

```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': 'logs/convites.log',
        },
    },
    'loggers': {
        'usuarios.services': {
            'handlers': ['file'],
            'level': 'INFO',
        },
        'usuarios.tasks': {
            'handlers': ['file'],
            'level': 'INFO',
        },
    },
}
```

---

## 🔐 Segurança

### **Implementado:**
- ✅ Token UUID4 (128-bit entropy)
- ✅ Expiração configurável (7 dias padrão)
- ✅ One-time use (token invalidado após aceite)
- ✅ Validações de permissão em múltiplas camadas
- ✅ Audit trail (IP, user agent)
- ✅ Constraint de unicidade (1 convite pendente por email+empresa)
- ✅ HTTPS obrigatório em produção (token em URL)
- ✅ Password validation (Django validators)

### **Recomendado para produção:**
- [ ] Rate limiting (django-ratelimit)
- [ ] CAPTCHA em convites (se muitas falhas)
- [ ] SPF/DKIM/DMARC para email
- [ ] Monitoramento de bounces

---

## 📈 Próximos Passos (Melhorias Futuras)

### **Fase 2:**
- [ ] Bulk invite (CSV upload)
- [ ] Mensagens personalizadas em convites
- [ ] Analytics dashboard (taxa de conversão)
- [ ] Notificações in-app (convite aceito)
- [ ] Rebranding de emails por empresa
- [ ] Multi-idioma (i18n)

### **Fase 3:**
- [ ] SSO (Google, Microsoft)
- [ ] Magic link login
- [ ] Aprovação de convites (workflow)
- [ ] Webhook callbacks

---

## 🎉 Conclusão

O sistema está **100% funcional** e pronto para uso!

**Arquivos criados/modificados:**
- ✅ `usuarios/models.py` - Model Convite
- ✅ `usuarios/services/convite_service.py` - Lógica de negócio
- ✅ `usuarios/tasks.py` - Celery tasks
- ✅ `usuarios/forms.py` - Forms de convite
- ✅ `usuarios/views_convite.py` - Views
- ✅ `usuarios/urls.py` - URLs
- ✅ `usuarios/admin.py` - Admin
- ✅ `core/templates/emails/convite.html` - Email HTML
- ✅ `core/templates/emails/convite.txt` - Email texto
- ✅ `core/templates/usuarios/listar_convites.html` - Lista
- ✅ `core/templates/convites/aceitar_convite.html` - Signup
- ✅ `core/templates/convites/convite_invalido.html` - Erro
- ✅ `cinnamon/celery.py` - Configuração Celery
- ✅ `cinnamon/settings.py` - Settings atualizadas
- ✅ `requirements.txt` - Dependências

**Pronto para testar!** 🚀
